"""Filing catalog (P1-09).

The catalog is the authority on *which filings exist and when they became
public*. It is what makes `as_of` correctness possible (D2): eligibility is
decided at filing-selection time from `filing_date`, with no re-indexing and
no change to the Qdrant payload.

Phase 0 established why this is needed: a stored chunk carries none of
`filing_date`, `period_end` or `accession_number` (K4), so a retrieved chunk
cannot be dated and look-ahead cannot be prevented at retrieval time.
"""

from catalog.store import CatalogStore, Filing

__all__ = ["CatalogStore", "Filing"]
