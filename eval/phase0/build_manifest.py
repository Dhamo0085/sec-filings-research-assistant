"""
Phase 0 / Step 1.3 (+ input for 3.3 lookahead scoring) — filing manifest.

Queries SEC EDGAR submissions for every bundled ticker and records the 10-K
history: accession, period_end (CONFORMED PERIOD OF REPORT equivalent),
filing_date, and the fiscal_year that ingestion/downloader._extract_fiscal_year
WOULD assign (calendar year of period_end -- its priority-1 SGML path).

Read-only. Writes reports/phase0/filing_manifest.{json,csv} and caches raw
submissions JSON under .cache/edgar/.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import date
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / ".cache" / "edgar"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = REPO_ROOT / "reports" / "phase0"

# SEC fair-access requires a contact-shaped User-Agent. config.Settings.edgar_email
# is the designated source for this, but no .env exists in this checkout, so a
# clearly-marked placeholder is used. SEE REPORT: edgar_email MUST be set.
UA = "Financial-RAG Phase0 audit phase0-audit@example.com"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}

# CIKs for the 12 bundled companies (config.COMPANIES) + NFLX (question S1).
# Each is verified against the `name` field SEC returns.
CIKS = {
    "AAPL": 320193, "MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724,
    "JPM": 19617, "WFC": 72971, "BAC": 70858, "GS": 886982,
    "BLK": 1364742, "STT": 93751, "TROW": 1113169, "IVZ": 914208,
    "NFLX": 1065280,
}
# BlackRock reorganised in 2024 into a new registrant; check the successor too.
ALT_CIKS = {"BLK": [2012383]}


def fetch_submissions(cik: int) -> dict | None:
    f = CACHE / f"CIK{cik:010d}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    time.sleep(1.1)  # SEC fair-access: <= 10 req/s, we use ~1 req/s
    if r.status_code != 200:
        print(f"  ! CIK {cik}: HTTP {r.status_code}")
        return None
    f.write_text(r.text, encoding="utf-8")
    return r.json()


def fetch_older(cik: int, name: str) -> dict | None:
    """submissions JSON paginates: older filings live in filings.files[].name"""
    f = CACHE / name
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    url = f"https://data.sec.gov/submissions/{name}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    time.sleep(1.1)
    if r.status_code != 200:
        print(f"  ! older chunk {name}: HTTP {r.status_code}")
        return None
    f.write_text(r.text, encoding="utf-8")
    return r.json()


def _rows_from_block(ticker, cik, registrant, rec):
    rows = []
    for form, acc, rdate, fdate, pdoc in zip(
        rec["form"], rec["accessionNumber"], rec["reportDate"],
        rec["filingDate"], rec["primaryDocument"]
    ):
        if form not in ("10-K", "10-K405", "10-KSB"):
            continue
        fy = int(rdate[:4]) if rdate else None
        rows.append({
            "ticker": ticker,
            "cik": cik,
            "registrant": registrant,
            "form": form,
            "accession_number": acc,
            "period_end": rdate,
            "filing_date": fdate,
            "fiscal_year_v1_rule": fy,          # calendar year of period end
            "primary_document": pdoc,
            "primary_doc_url": (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{acc.replace('-', '')}/{pdoc}"
            ),
            "filing_year": int(fdate[:4]) if fdate else None,
            "fy_differs_from_filing_year": (
                fy is not None and fdate and fy != int(fdate[:4])
            ),
        })
    return rows


def tenk_rows(ticker: str, cik: int, data: dict) -> list[dict]:
    """Recent block + every older pagination chunk."""
    registrant = data.get("name", "")
    rows = _rows_from_block(ticker, cik, registrant, data["filings"]["recent"])
    for chunk in data["filings"].get("files", []):
        older = fetch_older(cik, chunk["name"])
        if older:
            rows.extend(_rows_from_block(ticker, cik, registrant, older))
    return rows


def main() -> None:
    VALID_YEARS = {2023, 2024, 2025}   # routing/classifier.py:23
    today = date.today().isoformat()
    all_rows: list[dict] = []
    latest: list[dict] = []

    for ticker, cik in CIKS.items():
        print(f"{ticker} (CIK {cik}) ...")
        ciks = [cik] + ALT_CIKS.get(ticker, [])
        rows: list[dict] = []
        for c in ciks:
            d = fetch_submissions(c)
            if d:
                print(f"    registrant: {d.get('name')}")
                rows.extend(tenk_rows(ticker, c, d))
        rows.sort(key=lambda r: r["filing_date"], reverse=True)
        all_rows.extend(rows)
        if rows:
            top = rows[0]
            latest.append({
                "ticker": ticker,
                "latest_fiscal_year_on_edgar": top["fiscal_year_v1_rule"],
                "latest_period_end": top["period_end"],
                "latest_filing_date": top["filing_date"],
                "latest_accession": top["accession_number"],
                "reachable_by_classifier": top["fiscal_year_v1_rule"] in VALID_YEARS,
                "fiscal_years_on_edgar_last_5": [r["fiscal_year_v1_rule"] for r in rows[:5]],
                "indexed_locally": False,   # data/raw is empty in this checkout
            })

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_on": today,
        "source": "SEC EDGAR data.sec.gov/submissions (read-only)",
        "classifier_valid_years": sorted(VALID_YEARS),
        "local_index_state": "EMPTY — data/raw, data/parsed, data/chunks and data/qdrant "
                             "do not exist in this checkout",
        "latest_per_ticker": latest,
        "all_10k_filings": all_rows,
    }
    (OUT / "filing_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (OUT / "filing_manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n{'TICKER':7s} {'LATEST_FY':10s} {'PERIOD_END':12s} {'FILED':12s} {'CLASSIFIER_CAN_REACH':20s}")
    for r in latest:
        print(f"{r['ticker']:7s} {str(r['latest_fiscal_year_on_edgar']):10s} "
              f"{r['latest_period_end']:12s} {r['latest_filing_date']:12s} "
              f"{('YES' if r['reachable_by_classifier'] else 'NO -- UNREACHABLE'):20s}")
    unreachable = [r['ticker'] for r in latest if not r['reachable_by_classifier']]
    print(f"\nTickers whose LATEST 10-K the classifier cannot request: {unreachable or 'none'}")
    print(f"Total 10-K filings in manifest: {len(all_rows)}")


if __name__ == "__main__":
    main()
