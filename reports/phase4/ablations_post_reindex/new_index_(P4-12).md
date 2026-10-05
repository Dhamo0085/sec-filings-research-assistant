### Retrieval ablations — new index (P4-12)

| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |
|---|---:|---:|---:|---:|---:|---|
| `bm25` | 17/45 (38%) | 0.334 | 0/0 (0%) | 0 | 11.2 | lexical only — the floor, and the arm that should win on exact figures |
| `dense` | 31/45 (69%) | 0.544 | 0/0 (0%) | 0 | 3.7 | embeddings only — no lexical signal at all |
| `hybrid` | 29/45 (64%) | 0.494 | 0/0 (0%) | 0 | 7.7 | dense + BM25 with RRF fusion, nothing after it |
| `hybrid_rerank` | 35/45 (78%) | 0.604 | 0/0 (0%) | 0 | 268.4 | ...plus the cross-encoder |
| `hybrid_rerank_focus` | 44/45 (98%) | 0.870 | 0/0 (0%) | 0 | 278.9 | ...plus the focus boost — the shipped default |
| `hybrid_rerank_focus_nopar` | 44/45 (98%) | 0.870 | 0/0 (0%) | 0 | 286.7 | the default, with parent-section context off: does handing the model the whole section help, given that P3-00 found many section slices wrong? |
