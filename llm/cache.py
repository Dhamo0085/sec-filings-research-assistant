"""On-disk LLM response cache (P1-04, spec section 9 rule 2).

Keyed by a hash of (provider, model, messages, temperature, prompt_version,
response_format). A cache hit costs zero tokens, which is what makes the
Phase 4 ablations affordable: V0-V3 share most prompts, so only the first
variant pays for them (spec section 11).

SQLite rather than JSONL because the Phase 0 cache was append-only JSONL and
had to be fully re-read to answer one lookup.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key            TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT,
    content        TEXT NOT NULL,
    prompt_tokens  INTEGER,
    completion_tokens INTEGER,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_responses_model ON responses(provider, model);
"""


def cache_key(
    *,
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    prompt_version: str = "",
    response_format: Optional[str] = None,
) -> str:
    """Stable key. Model and provider are included so a failover to a
    different model is a different cache entry, not a silent substitution."""
    blob = json.dumps(
        {
            "provider": provider,
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "prompt_version": prompt_version,
            "response_format": response_format,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        with self._lock, self._connect() as con:
            con.executescript(_SCHEMA)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT content, prompt_tokens, completion_tokens, provider, model "
                "FROM responses WHERE key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def put(
        self,
        key: str,
        *,
        provider: str,
        model: str,
        content: str,
        prompt_version: str = "",
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO responses "
                "(key, provider, model, prompt_version, content, prompt_tokens, "
                " completion_tokens, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (key, provider, model, prompt_version, content, prompt_tokens,
                 completion_tokens, time.time()),
            )

    def stats(self) -> Dict[str, int]:
        with self._lock, self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        return {"entries": int(n)}
