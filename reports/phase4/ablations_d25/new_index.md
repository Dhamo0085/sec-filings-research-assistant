### Retrieval ablations — new index

| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |
|---|---:|---:|---:|---:|---:|---|
| `bm25` | 22/40 (55%) | 0.435 | 21/22 (95%) | 0 | 10.0 | lexical only — the floor, and the arm that should win on exact figures |
| `dense` | 32/40 (80%) | 0.667 | 21/22 (95%) | 0 | 2.2 | embeddings only — no lexical signal at all |
| `hybrid` | 31/40 (78%) | 0.615 | 21/22 (95%) | 0 | 4.9 | dense + BM25 with RRF fusion, nothing after it |
| `hybrid_rerank` | 33/40 (82%) | 0.640 | 21/22 (95%) | 0 | 327.3 | ...plus the cross-encoder |
| `hybrid_rerank_focus` | 37/40 (92%) | 0.799 | 21/22 (95%) | 0 | 358.4 | ...plus the focus boost — the shipped default |
| `hybrid_rerank_focus_nopar` | 37/40 (92%) | 0.799 | 17/22 (77%) | 0 | 322.5 | the default, with parent-section context off: does handing the model the whole section help, given that P3-00 found many section slices wrong? |
