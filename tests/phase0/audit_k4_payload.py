"""
Phase 0 / Step 1.5 — K4: payload audit.

data/qdrant does not exist in this checkout, so no STORED point can be read.
This is nonetheless decidable statically: retrieval/vector_store.py:272 stores
    payload=chunk.model_dump(exclude={"chunk_id"})
so the stored payload keys are exactly the Chunk model's fields minus chunk_id.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import install_heavy_stubs  # noqa: E402

install_heavy_stubs()

from models import Chunk, ParsedDocument  # noqa: E402

OUT = REPO_ROOT / "reports" / "phase0" / "k4_payload.json"
WANTED = ["filing_date", "period_end", "accession_number"]


def main() -> None:
    chunk = Chunk(
        parent_id="item_8_financials", doc_id="doc-1", text="...", company="Apple Inc.",
        ticker="AAPL", filing_type="10-K", fiscal_year=2024,
        section_name="item_8_financials", chunk_type="table", token_count=120,
    )
    payload = chunk.model_dump(exclude={"chunk_id"})
    doc_fields = sorted(ParsedDocument.model_fields.keys())

    res = {
        "stored_payload_source": "retrieval/vector_store.py:272  "
                                 "payload=chunk.model_dump(exclude={'chunk_id'})",
        "stored_point_payload_keys": sorted(payload.keys()),
        "chunk_model_fields": sorted(Chunk.model_fields.keys()),
        "parsed_document_fields": doc_fields,
        "collection_naming": "{TICKER}_{fiscal_year}  (retrieval/vector_store.py)",
        "presence_in_stored_payload": {
            f: {"in_chunk_payload": f in payload,
                "in_parsed_document": f in doc_fields}
            for f in WANTED
        },
        "verdict": (
            "K4 CONFIRMED — none of filing_date / period_end / accession_number reach a "
            "stored point. filing_date and accession_number EXIST on ParsedDocument but "
            "are dropped at chunk construction; period_end does not exist anywhere in the "
            "data model. A retrieved chunk therefore cannot be dated, so as_of filtering "
            "is impossible at retrieval time (supports decision D2)."
        ),
        "note_no_live_point": "data/qdrant is absent in this checkout, so no stored point "
                              "was read; the result follows from the model_dump above.",
    }
    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
