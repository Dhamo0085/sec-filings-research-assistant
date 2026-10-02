"""Build the filing catalog from SEC EDGAR submissions (P1-09).

    python -m catalog.build                 # bundled tickers
    python -m catalog.build --ticker NVDA   # one more filer
    python -m catalog.build --offline       # cache only, no network

Seeded from Phase 0's ``eval/phase0/build_manifest.py``, with the pieces that
Phase 0 had to discover the hard way built in:

* **Pagination.** ``filings.recent`` holds only the newest slice; older filings
  live in ``filings.files[]`` chunks. Phase 0's first run missed them and
  reported 1 filing for JPMorgan instead of its full history.
* **Dual CIKs.** A ticker can span two registrants (BlackRock), so every CIK in
  ``catalog/cik_overrides.yaml`` is fetched and merged.
* **SEC fair access.** ``www.sec.gov`` returns HTTP 403 without a
  contact-shaped User-Agent, so ``edgar_email`` is required. Requests are
  rate-limited and raw JSON is cached, so a rebuild is free.
* **Amendments.** 10-K/A rows are linked to the 10-K they amend by period, so
  D14 restatement handling has what it needs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests
import yaml
from loguru import logger

from catalog.store import ANNUAL_FORMS, CatalogStore, Filing, fiscal_label_from_period_end
from config import require_edgar_email, settings
from ingestion.sec_cache import SecResponseError, is_poisoned_json_cache, validated_json

_PACKAGE_DIR = Path(__file__).resolve().parent
_OVERRIDES_FILE = _PACKAGE_DIR / "cik_overrides.yaml"

# SEC asks for no more than 10 requests/second; the spec caps us at 5.
_MIN_REQUEST_INTERVAL = 1.0 / 5.0
_CACHE_DIR = Path(settings.data_dir).parent / ".cache" / "edgar"


def _user_agent() -> str:
    """SEC fair-access User-Agent.

    Must carry a contact address: Phase 0 observed HTTP 403 from
    www.sec.gov/Archives without one, and had to fall back to a placeholder
    because no .env existed.
    """
    return f"sec-filings-research-assistant (contact: {require_edgar_email()})"


def load_overrides() -> Dict[str, Dict]:
    with _OVERRIDES_FILE.open("r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("tickers", {}) or {}


def ciks_for_ticker(ticker: str, overrides: Optional[Dict] = None) -> List[int]:
    """Every CIK whose filings belong to this ticker, primary first."""
    overrides = overrides if overrides is not None else load_overrides()
    entry = overrides.get(ticker.upper())
    if not entry:
        return []
    out = [int(entry["primary"])]
    out.extend(int(c) for c in (entry.get("also") or []))
    return out


class EdgarFetcher:
    """Submissions fetcher: polite, cached, and offline-capable."""

    def __init__(
        self,
        cache_dir: Path = _CACHE_DIR,
        *,
        offline: bool = False,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._session = session or requests.Session()
        self._last_request = 0.0
        self.requests_made = 0

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - gap)
        self._last_request = time.monotonic()

    def _fetch_json(self, url: str, cache_name: str) -> Optional[Dict]:
        cached = self.cache_dir / cache_name
        if cached.exists():
            # A cache entry written before P3-00b may itself be an SEC error
            # page, so it is validated on read as well as on write.
            poisoned = is_poisoned_json_cache(cached)
            if poisoned is None:
                return json.loads(cached.read_text(encoding="utf-8"))
            logger.warning(f"catalog: discarding unusable cache {cached.name} ({poisoned})")
        if self.offline:
            logger.warning(f"catalog: offline and {cache_name} is not cached")
            return None
        self._throttle()
        resp = self._session.get(
            url,
            headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"},
            timeout=60,
        )
        self.requests_made += 1
        if resp.status_code != 200:
            logger.error(f"catalog: {url} -> HTTP {resp.status_code}")
            return None
        # Validate BEFORE writing (P3-00b): SEC serves its rate-limit page with
        # HTTP 200, and the previous order cached that page and then raised on
        # resp.json(), leaving a poisoned entry behind.
        try:
            data = validated_json(resp.text, url=url)
        except SecResponseError as exc:
            logger.error(f"catalog: {exc}")
            return None
        cached.write_text(resp.text, encoding="utf-8")
        return data

    def submissions(self, cik: int) -> Optional[Dict]:
        return self._fetch_json(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            f"CIK{cik:010d}.json",
        )

    def submissions_chunk(self, name: str) -> Optional[Dict]:
        return self._fetch_json(f"https://data.sec.gov/submissions/{name}", name)


def _blocks(data: Dict, fetcher: EdgarFetcher) -> Iterable[Tuple[Dict, str]]:
    """The recent block plus every older pagination chunk."""
    filings = data.get("filings") or {}
    recent = filings.get("recent")
    if recent:
        yield recent, "recent"
    for chunk in filings.get("files") or []:
        name = chunk.get("name")
        if not name:
            continue
        older = fetcher.submissions_chunk(name)
        if older:
            # Older chunks are the bare field arrays, not wrapped in "recent".
            block = older.get("filings", {}).get("recent", older)
            yield block, name


def rows_from_submissions(
    ticker: str, cik: int, data: Dict, fetcher: EdgarFetcher
) -> List[Filing]:
    entity_name = data.get("name") or ""
    out: List[Filing] = []
    for block, _source in _blocks(data, fetcher):
        forms = block.get("form") or []
        accs = block.get("accessionNumber") or []
        rdates = block.get("reportDate") or []
        fdates = block.get("filingDate") or []
        pdocs = block.get("primaryDocument") or []
        n = min(len(forms), len(accs), len(rdates), len(fdates))
        for i in range(n):
            form = forms[i]
            if form not in ANNUAL_FORMS:
                continue
            period_end = (rdates[i] or "").strip()
            filing_date = (fdates[i] or "").strip()
            if not period_end or not filing_date:
                # Without both dates the row cannot answer "what period?" or
                # "was it public?", so it is not usable for as_of work.
                logger.warning(
                    f"catalog: skipping {ticker} {accs[i]} — missing period_end "
                    f"or filing_date"
                )
                continue
            out.append(Filing(
                accession=accs[i],
                ticker=ticker.upper(),
                cik=int(cik),
                entity_name=entity_name,
                form_type=form,
                period_end=period_end,
                fiscal_label=fiscal_label_from_period_end(period_end),
                fiscal_label_source="period_end",
                filing_date=filing_date,
                primary_doc=pdocs[i] if i < len(pdocs) else None,
            ))
    return out


def link_amendments(filings: List[Filing]) -> List[Filing]:
    """Point each 10-K/A at the 10-K it amends (same ticker and period).

    D14 needs this so a restatement can be recognised as one rather than
    looking like a second, independent filing for the same year.
    """
    originals: Dict[Tuple[str, str], Filing] = {}
    for f in filings:
        if not f.is_amendment:
            key = (f.ticker, f.period_end)
            prev = originals.get(key)
            if prev is None or f.filing_date < prev.filing_date:
                originals[key] = f
    for f in filings:
        if f.is_amendment:
            original = originals.get((f.ticker, f.period_end))
            if original is not None:
                f.amends = original.accession
    return filings


def assign_collection_names(filings: Iterable[Filing], existing: Iterable[str]) -> int:
    """Map filings to already-indexed Qdrant collections ({TICKER}_{year}).

    Only the ORIGINAL filing for a period gets the collection: v1 indexes one
    collection per (ticker, fiscal_year), so an amendment shares it and must
    not claim it as its own.
    """
    available = set(existing)
    linked = 0
    by_period: Dict[Tuple[str, int], List[Filing]] = {}
    for f in filings:
        by_period.setdefault((f.ticker, f.fiscal_label), []).append(f)
    for (ticker, label), group in by_period.items():
        name = f"{ticker}_{label}"
        if name not in available:
            continue
        group.sort(key=lambda f: (f.is_amendment, f.filing_date))
        group[0].collection_name = name
        linked += 1
    return linked


def build(
    tickers: Optional[List[str]] = None,
    *,
    catalog_path: Optional[Path] = None,
    offline: bool = False,
    fetcher: Optional[EdgarFetcher] = None,
    collections: Optional[List[str]] = None,
) -> Tuple[CatalogStore, Dict[str, int]]:
    overrides = load_overrides()
    tickers = [t.upper() for t in (tickers or sorted(overrides.keys()))]
    fetcher = fetcher or EdgarFetcher(offline=offline)
    store = CatalogStore(Path(catalog_path or settings.catalog_path))

    all_rows: List[Filing] = []
    skipped: List[str] = []
    for ticker in tickers:
        ciks = ciks_for_ticker(ticker, overrides)
        if not ciks:
            logger.warning(
                f"catalog: no CIK for {ticker}; add it to catalog/cik_overrides.yaml"
            )
            skipped.append(ticker)
            continue
        for cik in ciks:
            data = fetcher.submissions(cik)
            if not data:
                continue
            rows = rows_from_submissions(ticker, cik, data, fetcher)
            logger.info(f"catalog: {ticker} CIK {cik} ({data.get('name')}) "
                        f"-> {len(rows)} annual filing(s)")
            all_rows.extend(rows)

    all_rows = link_amendments(all_rows)

    if collections is None:
        try:
            from retrieval.vector_store import list_collections
            collections = list(list_collections())
        except Exception as exc:
            logger.warning(f"catalog: could not list collections ({exc}); "
                           f"collection_name will stay unset")
            collections = []
    linked = assign_collection_names(all_rows, collections)

    store.upsert(all_rows)
    stats = store.stats()
    stats.update({
        "rows_seen": len(all_rows),
        "collections_linked": linked,
        "edgar_requests": fetcher.requests_made,
        "tickers_skipped": len(skipped),
    })
    return store, stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the filing catalog from EDGAR.")
    ap.add_argument("--ticker", action="append", dest="tickers",
                    help="limit to this ticker (repeatable)")
    ap.add_argument("--catalog-path", default=None)
    ap.add_argument("--offline", action="store_true",
                    help="use only cached EDGAR JSON; make no requests")
    args = ap.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

    try:
        store, stats = build(
            tickers=args.tickers,
            catalog_path=Path(args.catalog_path) if args.catalog_path else None,
            offline=args.offline,
        )
    except Exception as exc:
        logger.error(f"catalog build failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"\ncatalog: {store.path}")
    for key in ("filings", "amendments", "tickers", "with_collection",
                "collections_linked", "edgar_requests", "tickers_skipped"):
        print(f"  {key:20s} {stats.get(key)}")

    print(f"\n{'TICKER':7s} {'LABELS':40s} LATEST FILED")
    for ticker in store.tickers():
        filings = store.for_ticker(ticker)
        labels = sorted({f.fiscal_label for f in filings})
        shown = ", ".join(str(x) for x in labels[-8:])
        latest = filings[0] if filings else None
        print(f"{ticker:7s} {shown:40s} "
              f"{latest.period_end if latest else '-':12s} "
              f"{latest.filing_date if latest else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
