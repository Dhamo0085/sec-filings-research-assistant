"""
Phase 0 / Step 2 support — fetch primary 10-K documents into .cache/filings/.

data/raw is EMPTY in this checkout, so Step 2 has nothing local to audit.
To still answer decision D1 (can we extract numeric facts from the primary
documents the pipeline downloads?), we fetch the same documents the pipeline
would fetch into .cache/ -- an allowed Phase 0 output directory. data/raw is
never written.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DEST = REPO_ROOT / ".cache" / "filings"
DEST.mkdir(parents=True, exist_ok=True)
MANIFEST = json.loads((REPO_ROOT / "reports" / "phase0" / "filing_manifest.json").read_text())

HEADERS = {"User-Agent": "Financial-RAG Phase0 audit phase0-audit@example.com",
           "Accept-Encoding": "gzip, deflate"}

# (ticker, fiscal_year) pairs needed by the Step 3 gold questions, spanning
# Technology / Banking / Asset Management, plus WFC for the EX-13 case.
WANTED = [
    ("AAPL", 2024), ("MSFT", 2024), ("GOOGL", 2024), ("GOOGL", 2023),
    ("AMZN", 2023), ("JPM", 2024), ("BAC", 2024), ("WFC", 2024),
    ("BLK", 2024), ("GS", 2024), ("NFLX", 2024),
]


def main() -> None:
    rows = MANIFEST["all_10k_filings"]
    index = []
    for ticker, fy in WANTED:
        match = next((r for r in rows if r["ticker"] == ticker and r["fiscal_year_v1_rule"] == fy), None)
        if not match:
            print(f"  ! no manifest row for {ticker} FY{fy}")
            continue
        out = DEST / f"{ticker}_{fy}_{match['accession_number']}.htm"
        if not out.exists():
            r = requests.get(match["primary_doc_url"], headers=HEADERS, timeout=120)
            time.sleep(1.1)
            if r.status_code != 200:
                print(f"  ! {ticker} FY{fy}: HTTP {r.status_code} for {match['primary_doc_url']}")
                continue
            out.write_bytes(r.content)
        index.append({**match, "local_path": str(out.relative_to(REPO_ROOT)),
                      "size_bytes": out.stat().st_size})
        print(f"  ok {ticker} FY{fy}  {out.stat().st_size/1e6:.1f} MB  {out.name}")

    (DEST / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nFetched {len(index)} primary documents into {DEST.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
