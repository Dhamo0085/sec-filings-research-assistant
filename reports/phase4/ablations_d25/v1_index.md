### Retrieval ablations — v1 index

| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |
|---|---:|---:|---:|---:|---:|---|
| `bm25` | 20/40 (50%) | 0.370 | 21/22 (95%) | 0 | 7.8 | lexical only — the floor, and the arm that should win on exact figures |
| `dense` | 25/40 (62%) | 0.574 | 21/22 (95%) | 0 | 2.7 | embeddings only — no lexical signal at all |
| `hybrid` | 28/40 (70%) | 0.510 | 21/22 (95%) | 0 | 5.5 | dense + BM25 with RRF fusion, nothing after it |
| `hybrid_rerank` | 27/40 (68%) | 0.533 | 21/22 (95%) | 0 | 313.8 | ...plus the cross-encoder |
| `hybrid_rerank_focus` | 30/40 (75%) | 0.690 | 21/22 (95%) | 0 | 307.4 | ...plus the focus boost — the shipped default |
| `hybrid_rerank_focus_nopar` | 30/40 (75%) | 0.690 | 18/22 (82%) | 0 | 381.4 | the default, with parent-section context off: does handing the model the whole section help, given that P3-00 found many section slices wrong? |
