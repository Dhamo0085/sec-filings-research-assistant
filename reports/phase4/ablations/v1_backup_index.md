### Retrieval ablations — v1 backup index

| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |
|---|---:|---:|---:|---:|---:|---|
| `bm25` | 9/15 (60%) | 0.433 | 21/22 (95%) | 0 | 5.6 | lexical only — the floor, and the arm that should win on exact figures |
| `dense` | 12/15 (80%) | 0.747 | 21/22 (95%) | 0 | 1.5 | embeddings only — no lexical signal at all |
| `hybrid` | 12/15 (80%) | 0.544 | 21/22 (95%) | 0 | 2.8 | dense + BM25 with RRF fusion, nothing after it |
| `hybrid_rerank_focus` | 12/15 (80%) | 0.717 | 21/22 (95%) | 0 | 151.7 | ...plus the focus boost — the shipped default |
