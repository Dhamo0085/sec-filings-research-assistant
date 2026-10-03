### Retrieval ablations — current index

| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |
|---|---:|---:|---:|---:|---:|---|
| `bm25` | 9/15 (60%) | 0.433 | 21/22 (95%) | 0 | 5.9 | lexical only — the floor, and the arm that should win on exact figures |
| `dense` | 12/15 (80%) | 0.747 | 21/22 (95%) | 0 | 1.6 | embeddings only — no lexical signal at all |
| `hybrid` | 12/15 (80%) | 0.578 | 21/22 (95%) | 0 | 3.1 | dense + BM25 with RRF fusion, nothing after it |
| `hybrid_rerank` | 11/15 (73%) | 0.556 | 21/22 (95%) | 0 | 203.2 | ...plus the cross-encoder |
| `hybrid_rerank_focus` | 12/15 (80%) | 0.717 | 21/22 (95%) | 0 | 205.9 | ...plus the focus boost — the shipped default |
| `hybrid_rerank_focus_nopar` | 12/15 (80%) | 0.717 | 18/22 (82%) | 0 | 187.8 | the default, with parent-section context off: does handing the model the whole section help, given that P3-00 found many section slices wrong? |
