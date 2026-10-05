"""Catalog storage (P1-09, spec 6.1 and D12).

SQLite, one row per filing, keyed by accession. The two columns the rest of the
system depends on are ``filing_date`` (the `as_of` eligibility test, D2) and
``period_end`` (what period the filing reports).

Why a separate store rather than Qdrant payloads: D2. Eligibility is decided
when filings are selected, so adding `as_of` needs no re-indexing and no
payload migration. Phase 0 confirmed a stored chunk carries neither date (K4).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession           TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    cik                 INTEGER NOT NULL,
    entity_name         TEXT NOT NULL,
    form_type           TEXT NOT NULL,
    period_end          TEXT NOT NULL,          -- ISO date
    fiscal_label        INTEGER NOT NULL,
    fiscal_label_source TEXT NOT NULL,          -- 'dei' | 'period_end'
    filing_date         TEXT NOT NULL,          -- ISO date
    primary_doc         TEXT,
    exhibit_docs        TEXT NOT NULL DEFAULT '[]',   -- JSON list
    collection_name     TEXT,
    facts_built_at      TEXT,
    amends              TEXT                     -- accession this amends
);
CREATE INDEX IF NOT EXISTS idx_filings_ticker        ON filings(ticker);
CREATE INDEX IF NOT EXISTS idx_filings_filing_date   ON filings(filing_date);
CREATE INDEX IF NOT EXISTS idx_filings_period_end    ON filings(period_end);
CREATE INDEX IF NOT EXISTS idx_filings_ticker_label  ON filings(ticker, fiscal_label);
"""

ANNUAL_FORMS = ("10-K", "10-K/A")


@dataclass
class Filing:
    accession: str
    ticker: str
    cik: int
    entity_name: str
    form_type: str
    period_end: str
    fiscal_label: int
    fiscal_label_source: str
    filing_date: str
    primary_doc: Optional[str] = None
    exhibit_docs: List[str] = field(default_factory=list)
    collection_name: Optional[str] = None
    facts_built_at: Optional[str] = None
    amends: Optional[str] = None

    @property
    def is_amendment(self) -> bool:
        return self.form_type.endswith("/A")

    def eligible_at(self, as_of: Optional[str]) -> bool:
        """Was this filing public on `as_of`? (G2, D2)

        An ISO date string compares correctly lexicographically, so no parsing
        is needed. ``as_of=None`` means no restriction.
        """
        if not as_of:
            return True
        return self.filing_date <= as_of


def _row_to_filing(row: sqlite3.Row) -> Filing:
    data = dict(row)
    data["exhibit_docs"] = json.loads(data.get("exhibit_docs") or "[]")
    return Filing(**data)


class CatalogStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # ── writes ─────────────────────────────────────────────────────────────

    def upsert(self, filings: Iterable[Filing]) -> int:
        rows = [
            (
                f.accession, f.ticker, int(f.cik), f.entity_name, f.form_type,
                f.period_end, int(f.fiscal_label), f.fiscal_label_source,
                f.filing_date, f.primary_doc, json.dumps(f.exhibit_docs),
                f.collection_name, f.facts_built_at, f.amends,
            )
            for f in filings
        ]
        if not rows:
            return 0
        with self._lock, self._connect() as con:
            con.executemany(
                "INSERT INTO filings (accession, ticker, cik, entity_name, form_type,"
                " period_end, fiscal_label, fiscal_label_source, filing_date,"
                " primary_doc, exhibit_docs, collection_name, facts_built_at, amends)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(accession) DO UPDATE SET"
                "   ticker=excluded.ticker, cik=excluded.cik,"
                "   entity_name=excluded.entity_name, form_type=excluded.form_type,"
                "   period_end=excluded.period_end,"
                "   fiscal_label=excluded.fiscal_label,"
                "   fiscal_label_source=excluded.fiscal_label_source,"
                "   filing_date=excluded.filing_date,"
                "   primary_doc=excluded.primary_doc,"
                "   exhibit_docs=excluded.exhibit_docs,"
                "   amends=excluded.amends",
                rows,
            )
        return len(rows)

    def set_collection_name(self, accession: str,
                            collection_name: Optional[str]) -> None:
        """``None`` clears the link — the store no longer holds that collection."""
        with self._lock, self._connect() as con:
            con.execute("UPDATE filings SET collection_name=? WHERE accession=?",
                        (collection_name, accession))

    def set_fiscal_label(self, accession: str, fiscal_label: int,
                         source: str = "dei") -> None:
        """Replace a filing's fiscal label with the one the filer declared (P2-03).

        P1-09 seeded every label from ``period_end.year`` because the DEI facts
        were not extracted yet. D6 says the company's own label wins, so this
        records both the value and where it came from — a later reader can tell
        a declared 2026 from an inferred one.
        """
        if source not in ("dei", "period_end"):
            raise ValueError(f"fiscal_label_source must be 'dei' or 'period_end', "
                             f"got {source!r}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE filings SET fiscal_label = ?, fiscal_label_source = ? "
                "WHERE accession = ?",
                (int(fiscal_label), source, accession),
            )

    def set_exhibit_docs(self, accession: str, docs: Iterable[str]) -> None:
        """Record every iXBRL document of the submission beyond the primary one.

        Spec 6.1 has the field; P1-09 never filled it because nothing yet read
        FilingSummary.xml. The facts build needs it: Wells Fargo's numbers are
        in an exhibit, and a resolver that only knows the primary document
        would find 4 consolidated facts instead of 1,289.
        """
        import json as _json
        with self._connect() as conn:
            conn.execute(
                "UPDATE filings SET exhibit_docs = ? WHERE accession = ?",
                (_json.dumps(list(docs)), accession),
            )

    def mark_facts_built(self, accession: str, when: Optional[str] = None) -> None:
        stamp = when or datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock, self._connect() as con:
            con.execute("UPDATE filings SET facts_built_at=? WHERE accession=?",
                        (stamp, accession))

    # ── reads ──────────────────────────────────────────────────────────────

    def get(self, accession: str) -> Optional[Filing]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM filings WHERE accession=?",
                              (accession,)).fetchone()
        return _row_to_filing(row) if row else None

    def all_filings(self) -> List[Filing]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM filings ORDER BY ticker, period_end DESC"
            ).fetchall()
        return [_row_to_filing(r) for r in rows]

    def for_ticker(self, ticker: str, *, as_of: Optional[str] = None) -> List[Filing]:
        """Filings for a ticker, newest period first, eligible at `as_of`."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM filings WHERE ticker=? ORDER BY period_end DESC,"
                " filing_date DESC",
                (ticker.upper(),),
            ).fetchall()
        out = [_row_to_filing(r) for r in rows]
        return [f for f in out if f.eligible_at(as_of)]

    def resolve_period(
        self,
        ticker: str,
        *,
        fiscal_label: Optional[int] = None,
        as_of: Optional[str] = None,
    ) -> Optional[Filing]:
        """Best filing for a (ticker, fiscal_label) at `as_of`.

        Implements D14: among the eligible filings covering the period, the
        most recently FILED one wins, so a 10-K/A supersedes the original once
        it is public — but only if it was public at `as_of`.
        """
        candidates = [
            f for f in self.for_ticker(ticker, as_of=as_of)
            if fiscal_label is None or f.fiscal_label == fiscal_label
        ]
        if not candidates:
            return None
        if fiscal_label is None:
            latest_label = max(f.fiscal_label for f in candidates)
            candidates = [f for f in candidates if f.fiscal_label == latest_label]
        return max(candidates, key=lambda f: (f.filing_date, f.accession))

    def available_fiscal_labels(self, ticker: str, *,
                                as_of: Optional[str] = None) -> List[int]:
        return sorted({f.fiscal_label for f in self.for_ticker(ticker, as_of=as_of)})

    def tickers(self) -> List[str]:
        with self._connect() as con:
            rows = con.execute("SELECT DISTINCT ticker FROM filings ORDER BY ticker")
            return [r[0] for r in rows]

    def eligible_collections(
        self,
        tickers: Iterable[str],
        *,
        fiscal_labels: Optional[Iterable[int]] = None,
        as_of: Optional[str] = None,
    ) -> List[str]:
        """Collection names to search, filtered by `as_of` (D2).

        This is the join that makes look-ahead protection possible on the text
        path: the catalog knows each collection's filing_date, the Qdrant
        payload does not.
        """
        wanted = set(fiscal_labels) if fiscal_labels else None
        names: List[str] = []
        for ticker in tickers:
            for f in self.for_ticker(ticker, as_of=as_of):
                if wanted and f.fiscal_label not in wanted:
                    continue
                if f.collection_name and f.collection_name not in names:
                    names.append(f.collection_name)
        return names

    def stats(self) -> Dict[str, int]:
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            amendments = con.execute(
                "SELECT COUNT(*) FROM filings WHERE form_type LIKE '%/A'"
            ).fetchone()[0]
            tickers = con.execute(
                "SELECT COUNT(DISTINCT ticker) FROM filings"
            ).fetchone()[0]
            with_collection = con.execute(
                "SELECT COUNT(*) FROM filings WHERE collection_name IS NOT NULL"
            ).fetchone()[0]
        return {
            "filings": int(total),
            "amendments": int(amendments),
            "tickers": int(tickers),
            "with_collection": int(with_collection),
        }

    def to_dicts(self) -> List[Dict]:
        return [asdict(f) for f in self.all_filings()]


def fiscal_label_from_period_end(period_end: str) -> int:
    """Interim fiscal label: the calendar year of the period end.

    P1-09 specifies this for now. It is NOT the company's own label — Microsoft's
    fiscal year ending 2026-06-30 is labelled FY2026 by Microsoft and also by
    this rule, but a January-ending fiscal year would disagree. P2-03 replaces
    it with dei:DocumentFiscalYearFocus read from the filing, recording
    `fiscal_label_source` so the two can be told apart (D6).
    """
    return date.fromisoformat(period_end).year
