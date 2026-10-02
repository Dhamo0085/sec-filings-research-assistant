"""The facts store: SQLite, idempotent per accession (P2-04).

    store = FactsStore(settings.facts_db_path)
    if store.needs_build(accession, source_hash):
        store.replace_submission(accession, ticker, period_end, facts, ...)
    store.query(ticker="AAPL", concept="us-gaap:Revenues")

Schema is spec 6.2. Three choices in it are worth stating plainly:

* **``value`` is TEXT, not REAL.** It holds the exact decimal string, fully
  scaled and signed. ``value_num`` exists beside it as a REAL *for sorting and
  range filters only* — the column name says so and every read path returns the
  TEXT one as a ``Decimal``. A float revenue is a wrong revenue as soon as it is
  added to another one, and SQLite would happily give one back.
* **Only non-dimensional facts are stored** (spec 6.2 rule). The segment and
  product breakdowns are 80-95% of a filing's facts and none of them answer
  "what was revenue"; storing them would mean every resolver query had to
  re-filter them, and one missed filter would return a segment as the total.
* **A build is replace-per-accession, not insert.** ``replace_submission``
  deletes the accession's rows inside the same transaction that writes the new
  ones, so a rebuild cannot leave a half-updated filing behind and running it
  twice gives byte-identical content (T2-08).

Idempotence is keyed on a **hash of the source documents**, not on their
timestamps or the accession alone: the SEC can re-post a document, and "did the
bytes change" is the only question whose answer is trustworthy.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY,
    accession   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    concept     TEXT NOT NULL,
    value       TEXT NOT NULL,      -- exact decimal string, scaled and signed
    value_num   REAL,               -- for sorting and ranges ONLY
    unit        TEXT,
    decimals    TEXT,
    scale_raw   TEXT,
    sign_raw    TEXT,
    period_type TEXT NOT NULL,      -- duration | instant
    start_date  TEXT,
    end_date    TEXT,
    context_id  TEXT,
    source_doc  TEXT,
    element_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_ticker      ON facts(ticker);
CREATE INDEX IF NOT EXISTS idx_facts_concept     ON facts(concept);
CREATE INDEX IF NOT EXISTS idx_facts_end_date    ON facts(end_date);
CREATE INDEX IF NOT EXISTS idx_facts_accession   ON facts(accession);
-- The resolver's own query shape: "this filer, this concept, this period".
CREATE INDEX IF NOT EXISTS idx_facts_lookup
    ON facts(ticker, concept, end_date);

CREATE TABLE IF NOT EXISTS submissions (
    accession     TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    cik           INTEGER,
    form_type     TEXT,
    period_end    TEXT,
    fiscal_label  INTEGER,
    source_hash   TEXT NOT NULL,    -- hash of the document bytes
    documents     TEXT,             -- json list of document names
    fact_count    INTEGER NOT NULL,
    built_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_ticker ON submissions(ticker);
"""

# Columns written per fact, in order. Named once so the INSERT, the row
# mapping and the content hash cannot drift apart.
_FACT_COLUMNS = (
    "accession", "ticker", "concept", "value", "value_num", "unit", "decimals",
    "scale_raw", "sign_raw", "period_type", "start_date", "end_date",
    "context_id", "source_doc", "element_id",
)
# Pre-joined once. The two statements below interpolate this constant, which is
# why they carry `noqa: S608`: the interpolated text is this module's own tuple
# of column-name literals and no caller value reaches it. Every value is bound
# as a parameter. The `IN (...)` clauses in query() interpolate only question
# marks, for the same reason.
_FACT_COLUMN_LIST = ", ".join(_FACT_COLUMNS)


@dataclass(frozen=True)
class StoredFact:
    """A fact as it comes back out of the database."""

    accession: str
    ticker: str
    concept: str
    value: Decimal
    unit: Optional[str]
    period_type: str
    start_date: Optional[str]
    end_date: Optional[str]
    decimals: Optional[str] = None
    scale_raw: Optional[str] = None
    sign_raw: Optional[str] = None
    context_id: Optional[str] = None
    source_doc: Optional[str] = None
    element_id: Optional[str] = None

    @property
    def concept_local(self) -> str:
        return self.concept.rsplit(":", 1)[-1]


def source_hash(paths: Sequence[Path]) -> str:
    """Hash of the document bytes, order-independent.

    Order-independent on purpose: ``FilingSummary.xml`` could list a
    submission's documents in a different order on a re-fetch, and that is not
    a content change. The document *names* are included so adding an exhibit
    changes the hash even if the primary document did not.
    """
    digests = sorted(
        f"{Path(p).name}:{hashlib.sha256(Path(p).read_bytes()).hexdigest()}"
        for p in paths
    )
    return hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()


class FactsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── build bookkeeping ──────────────────────────────────────────────────

    def needs_build(self, accession: str, current_hash: str) -> bool:
        """True when this accession has never been built, or its bytes changed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_hash FROM submissions WHERE accession = ?",
                (accession,),
            ).fetchone()
        return row is None or row["source_hash"] != current_hash

    def built_submissions(self, ticker: Optional[str] = None) -> List[Dict]:
        sql = "SELECT * FROM submissions"
        params: tuple = ()
        if ticker:
            sql += " WHERE ticker = ?"
            params = (ticker.upper(),)
        sql += " ORDER BY ticker, period_end DESC"
        with self._connect() as conn:
            return [
                {**dict(r), "documents": json.loads(r["documents"] or "[]")}
                for r in conn.execute(sql, params)
            ]

    def replace_submission(
        self,
        *,
        accession: str,
        ticker: str,
        facts: Iterable,
        source_hash_value: str,
        documents: Sequence[str] = (),
        cik: Optional[int] = None,
        form_type: Optional[str] = None,
        period_end: Optional[str] = None,
        fiscal_label: Optional[int] = None,
    ) -> int:
        """Write one submission's facts, replacing anything already stored.

        Delete and insert happen in one transaction. A rebuild interrupted
        halfway would otherwise leave a filing with some old rows and some new
        ones — which no later query could detect, because both look like
        ordinary facts.
        """
        rows = []
        for fact in facts:
            if fact.value is None or fact.is_nil or not fact.is_numeric:
                continue
            if fact.is_dimensional:
                continue            # spec 6.2: consolidated facts only
            end_date = fact.end_date
            rows.append((
                accession,
                ticker.upper(),
                fact.concept,
                str(fact.value),
                float(fact.value),
                fact.unit,
                fact.decimals,
                fact.scale_raw,
                fact.sign_raw,
                fact.period_type or "",
                fact.start_date.isoformat() if fact.start_date else None,
                end_date.isoformat() if end_date else None,
                fact.context_id,
                fact.source_doc,
                fact.element_id,
            ))

        placeholders = ", ".join("?" * len(_FACT_COLUMNS))
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM facts WHERE accession = ?", (accession,))
            conn.executemany(
                f"INSERT INTO facts ({_FACT_COLUMN_LIST}) "  # noqa: S608
                f"VALUES ({placeholders})",
                rows,
            )
            conn.execute(
                "INSERT INTO submissions (accession, ticker, cik, form_type, "
                "period_end, fiscal_label, source_hash, documents, fact_count, "
                "built_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(accession) DO UPDATE SET "
                "ticker=excluded.ticker, cik=excluded.cik, "
                "form_type=excluded.form_type, period_end=excluded.period_end, "
                "fiscal_label=excluded.fiscal_label, "
                "source_hash=excluded.source_hash, documents=excluded.documents, "
                "fact_count=excluded.fact_count, built_at=excluded.built_at",
                (accession, ticker.upper(), cik, form_type, period_end,
                 fiscal_label, source_hash_value, json.dumps(list(documents)),
                 len(rows), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        return len(rows)

    def forget_submission(self, accession: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM facts WHERE accession = ?", (accession,))
            conn.execute("DELETE FROM submissions WHERE accession = ?", (accession,))
            conn.commit()

    # ── reads ──────────────────────────────────────────────────────────────

    def query(
        self,
        *,
        ticker: Optional[str] = None,
        concept: Optional[str] = None,
        concepts: Optional[Sequence[str]] = None,
        accession: Optional[str] = None,
        accessions: Optional[Sequence[str]] = None,
        end_date: Optional[str] = None,
        period_type: Optional[str] = None,
    ) -> List[StoredFact]:
        clauses: List[str] = []
        params: List = []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper())
        if concept:
            clauses.append("concept = ?")
            params.append(concept)
        if concepts:
            clauses.append(f"concept IN ({', '.join('?' * len(concepts))})")
            params.extend(concepts)
        if accession:
            clauses.append("accession = ?")
            params.append(accession)
        if accessions:
            clauses.append(f"accession IN ({', '.join('?' * len(accessions))})")
            params.extend(accessions)
        if end_date:
            clauses.append("end_date = ?")
            params.append(end_date)
        if period_type:
            clauses.append("period_type = ?")
            params.append(period_type)

        sql = "SELECT * FROM facts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # Deterministic order so callers and golden tests see stable results.
        sql += " ORDER BY ticker, concept, end_date, accession, id"
        with self._connect() as conn:
            return [self._to_fact(r) for r in conn.execute(sql, params)]

    @staticmethod
    def _to_fact(row: sqlite3.Row) -> StoredFact:
        return StoredFact(
            accession=row["accession"],
            ticker=row["ticker"],
            concept=row["concept"],
            # The TEXT column, never value_num: that one is for sorting.
            value=Decimal(row["value"]),
            unit=row["unit"],
            period_type=row["period_type"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            decimals=row["decimals"],
            scale_raw=row["scale_raw"],
            sign_raw=row["sign_raw"],
            context_id=row["context_id"],
            source_doc=row["source_doc"],
            element_id=row["element_id"],
        )

    def concepts_for(self, ticker: str) -> List[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT concept FROM facts WHERE ticker = ? "
                "ORDER BY concept", (ticker.upper(),))]

    def tickers(self) -> List[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM facts ORDER BY ticker")]

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            one = conn.execute(
                "SELECT COUNT(*) AS facts, COUNT(DISTINCT ticker) AS tickers, "
                "COUNT(DISTINCT concept) AS concepts, "
                "COUNT(DISTINCT accession) AS accessions FROM facts"
            ).fetchone()
            subs = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        return {**dict(one), "submissions": subs}

    # ── T2-08: identical rebuilds ──────────────────────────────────────────

    def content_hash(self) -> str:
        """Hash of every fact row, independent of insertion order or rowids.

        ``built_at`` and the autoincrement ``id`` are deliberately excluded:
        both change on every rebuild by construction, and including them would
        make T2-08 impossible to satisfy for reasons that say nothing about the
        data. What is hashed is the facts themselves.
        """
        digest = hashlib.sha256()
        with self._connect() as conn:
            for row in conn.execute(
                f"SELECT {_FACT_COLUMN_LIST} FROM facts "        # noqa: S608
                f"ORDER BY accession, concept, context_id, end_date, value, "
                f"element_id"
            ):
                digest.update("\x1f".join(
                    "" if value is None else str(value) for value in tuple(row)
                ).encode("utf-8"))
                digest.update(b"\x1e")
        return digest.hexdigest()

    def submission_hashes(self) -> Dict[str, str]:
        """accession -> source_hash, for reporting which filings are current."""
        with self._connect() as conn:
            return {r["accession"]: r["source_hash"]
                    for r in conn.execute(
                        "SELECT accession, source_hash FROM submissions")}
