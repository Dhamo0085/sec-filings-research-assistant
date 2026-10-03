"""Fetch the documents of a filing, politely and once (P2-03, P2-04 support).

    from facts.documents import DocumentFetcher
    paths = DocumentFetcher().ensure(filing)      # every iXBRL document
    submission = parse_submission(paths, accession=filing.accession)

**Why this is not ``ingestion/downloader.py``.** That module populates
``data/raw/`` for the text pipeline, and CLAUDE.md rule 6 makes ``data/raw/``
read-only. P2-04 says the same thing from the other side: "download extra
filings if needed; raw stays read-only". So facts documents land in
``.cache/filings/`` — the Phase 0/Phase 1 convention, gitignored, already
holding ~73 MB of valid SEC responses that are reused rather than re-fetched.

**Why ``FilingSummary.xml`` rather than guessing at the directory listing.**
A submission's facts can be spread across several documents, and the only
authoritative list of which ones carry XBRL is the filing's own
``FilingSummary.xml``. Its ``<InputFiles>`` block for Wells Fargo FY2024 reads:

    <File doctype="10-K" ... original="wfc-20241231.htm">wfc-20241231.htm</File>
    <File doctype="10-K" ... original="wfc-20241231_d2.htm">wfc-20241231_d2.htm</File>
    <File>wfc-20241231.xsd</File>  ... (the linkbases)

Two documents, both needed: the first holds 18 facts and the contexts, the
second holds 7,285 facts that reference them. The accession's ``index.json``
lists 229 entries for that filing, so filtering a directory listing by
extension would mean downloading megabytes of exhibits to find out which two
mattered. One 115 KB request answers it exactly.

SEC fair access: ``www.sec.gov`` returns HTTP 403 without a contact-shaped
User-Agent (Phase 0 measured this), requests are capped at 5/second, and every
response is cached so a re-run makes no requests at all.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests
from loguru import logger

from config import require_edgar_email, settings
from ingestion.sec_cache import SecResponseError, validated_document

# The spec caps us at 5 requests/second; the SEC's own guidance is 10.
_MIN_REQUEST_INTERVAL = 1.0 / 5.0
_CACHE_DIR = Path(settings.data_dir).parent / ".cache" / "filings"

_INPUT_FILES_BLOCK = re.compile(r"<InputFiles>(.*?)</InputFiles>", re.S | re.I)
_INPUT_FILE = re.compile(r"<File\b([^>]*)>(.*?)</File>", re.S | re.I)
_DOCTYPE_ATTR = re.compile(r'doctype\s*=\s*"([^"]*)"', re.I)
_IXBRL_SUFFIXES = (".htm", ".html")


class DocumentFetchError(RuntimeError):
    """A document or summary could not be retrieved."""


def _user_agent() -> str:
    return f"sec-filings-research-assistant (contact: {require_edgar_email()})"


def _accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def parse_input_files(summary_xml: str) -> List[str]:
    """Document names from a ``FilingSummary.xml``, iXBRL documents only.

    Linkbases (.xsd/.xml) are dropped: they describe the taxonomy, they carry
    no facts, and fetching them would multiply the request count by five for
    nothing.
    """
    block = _INPUT_FILES_BLOCK.search(summary_xml or "")
    if block is None:
        return []
    out: List[str] = []
    for attrs, name in _INPUT_FILE.findall(block.group(1)):
        name = name.strip()
        if not name or not name.lower().endswith(_IXBRL_SUFFIXES):
            continue
        # doctype is informational here; the extension already selects the
        # iXBRL documents, and a filer that omits doctype should not be
        # silently skipped.
        _ = _DOCTYPE_ATTR.search(attrs)
        if name not in out:
            out.append(name)
    return out


class DocumentFetcher:
    """Cached, rate-limited fetcher for a filing's iXBRL documents."""

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
        self.bytes_fetched = 0

    # ── plumbing ───────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - gap)
        self._last_request = time.monotonic()

    def _get(self, url: str, dest: Path) -> Optional[Path]:
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if self.offline:
            logger.warning(f"facts.documents: offline and {dest.name} is not cached")
            return None
        self._throttle()
        resp = self._session.get(
            url,
            headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"},
            timeout=120,
        )
        self.requests_made += 1
        if resp.status_code != 200:
            logger.error(f"facts.documents: {url} -> HTTP {resp.status_code}")
            return None
        # Validate before writing (P3-00b). SEC serves its rate-limit page with
        # HTTP 200; cached as a filing document it would only fail much later,
        # inside iXBRL extraction, with nothing pointing back at the cause.
        try:
            body = validated_document(resp.content, url=url)
        except SecResponseError as exc:
            logger.error(f"facts.documents: {exc}")
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        self.bytes_fetched += len(body)
        return dest

    def filing_dir(self, ticker: str, accession: str) -> Path:
        """One directory per accession, so exhibits keep their own names.

        Phase 0 used flat ``TICKER_YEAR_ACCESSION.htm`` names and had to invent
        a suffix for the Wells Fargo exhibit (``WFC_2024_EX13_d2.htm``). A
        directory per filing keeps the names the SEC uses, which is what
        ``FilingSummary.xml`` refers to.
        """
        return self.cache_dir / f"{ticker.upper()}_{accession}"

    # ── public API ─────────────────────────────────────────────────────────

    def base_url(self, cik: int, accession: str) -> str:
        return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{_accession_nodash(accession)}")

    def document_names(self, cik: int, accession: str, ticker: str) -> List[str]:
        """Every iXBRL document of the submission, from its FilingSummary.xml."""
        dest = self.filing_dir(ticker, accession) / "FilingSummary.xml"
        path = self._get(f"{self.base_url(cik, accession)}/FilingSummary.xml", dest)
        if path is None:
            return []
        try:
            return parse_input_files(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise DocumentFetchError(f"cannot read {dest}: {exc}") from exc

    def ensure(self, filing, *, max_documents: int = 8) -> List[Path]:
        """Local paths for the filing's iXBRL documents, downloading as needed.

        Falls back to ``filing.primary_doc`` when FilingSummary.xml is missing
        (older filings predate it), so a pre-2009 10-K still yields its one
        document instead of nothing.
        """
        names = self.document_names(filing.cik, filing.accession, filing.ticker)
        if not names and filing.primary_doc:
            logger.info(f"facts.documents: {filing.accession} has no FilingSummary; "
                        f"falling back to primary_doc {filing.primary_doc}")
            names = [filing.primary_doc]
        if not names:
            return []

        base = self.base_url(filing.cik, filing.accession)
        out: List[Path] = []
        for name in names[:max_documents]:
            path = self._get(f"{base}/{name}",
                             self.filing_dir(filing.ticker, filing.accession) / name)
            if path is not None:
                out.append(path)
        return out

    def stats(self) -> Dict[str, int]:
        return {"requests_made": self.requests_made,
                "bytes_fetched": self.bytes_fetched}


def legacy_cache_paths(ticker: str, fiscal_label: int) -> Sequence[Path]:
    """Phase 0's flat cache names, so its 12 documents are reused not re-fetched.

    Phase 0 wrote ``.cache/filings/AAPL_2024_0000320193-24-000123.htm`` and
    ``WFC_2024_EX13_d2.htm``. Those are ~73 MB of valid SEC responses; the spec
    says to reuse the cache before fetching again.
    """
    prefix = f"{ticker.upper()}_{fiscal_label}_"
    return sorted(p for p in _CACHE_DIR.glob(f"{prefix}*.htm") if p.is_file())
