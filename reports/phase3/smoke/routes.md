# Phase 3 smoke evaluation (P3-11)

Run 2026-10-02T21:22:59+00:00 against the real stack (real catalog, real facts store, real Qdrant index, real LLM).

Status shape only — no figure is scored here; that is Phase 4 (P4-03).

| id | category | question | as_of | status | reason / type | verdict | s |
|---|---|---|---|---|---|---|---|
| S01 | numeric | What was Apple's revenue in fiscal 2024? | - | routed | numeric_fact | routed | 0.01 |
| S02 | numeric | What was Microsoft's net income in fiscal 2024? | - | routed | numeric_fact | routed | 0.009 |
| S03 | numeric | What was JPMorgan's total net revenue for fiscal 2024? | - | routed | numeric_fact | routed | 0.008 |
| S04 | numeric | What was Netflix's revenue in fiscal 2024? | - | routed | numeric_fact | routed | 0.007 |
| S05 | numeric | What was Bank of America's total revenue in 2024? | - | routed | numeric_fact | routed | 0.007 |
| S06 | numeric | What was Goldman Sachs' total net revenues in fiscal 2024? | - | routed | numeric_fact | routed | 0.008 |
| S07 | numeric | What was Apple's diluted EPS in fiscal 2024? | - | routed | numeric_fact | routed | 0.007 |
| S08 | numeric | What was Amazon's operating cash flow in fiscal 2024? | - | routed | numeric_fact | routed | 0.008 |
| S09 | computed | What was Apple's operating margin in fiscal 2024? | - | routed | computed | routed | 0.007 |
| S10 | computed | How did Apple's revenue grow from fiscal 2023 to fiscal 2024? | - | routed | trend | routed | 0.008 |
| S11 | computed | What was Microsoft's revenue CAGR from 2024 to 2026? | - | routed | trend | routed | 0.007 |
| S12 | compare | Compare Apple and Microsoft revenue in fiscal 2024 | - | routed | compare | routed | 0.008 |
| S13 | compare | Compare JPMorgan and Bank of America net income in 2024 | - | routed | compare | routed | 0.008 |
| S14 | trend | How has Apple's revenue moved from 2023 to 2025? | - | routed | trend | routed | 0.008 |
| S15 | narrative | What risks does Apple disclose about its supply chain? | - | routed | narrative | routed | 0.007 |
| S16 | narrative | What does Amazon say about competition in its business? | - | routed | narrative | routed | 0.007 |
| S17 | narrative | What does Apple disclose about cybersecurity? | - | routed | narrative | routed | 0.008 |
| S18 | narrative | Summarize Goldman Sachs' key business segments | - | routed | narrative | routed | 0.007 |
| S19 | narrative | What does BlackRock say about its business in fiscal 2023? | - | routed | narrative | routed | 0.008 |
| S20 | as_of | What was Apple's revenue in fiscal 2024? | 2024-06-30 | routed | numeric_fact | routed | 0.007 |
| S21 | as_of | As of March 1 2025, what was Apple's latest annual revenue? | - | routed | numeric_fact | routed | 0.008 |
| S22 | as_of | What was Apple's latest annual revenue? | 2023-12-31 | routed | numeric_fact | routed | 0.008 |
| S23 | as_of | What risks does Apple disclose? | 2024-06-30 | routed | narrative | routed | 0.008 |
| S24 | abstain | What was Apple's Q3 2024 revenue? | - | routed | unsupported_period_type | routed | 0.007 |
| S25 | abstain | Should I buy Apple stock? | - | routed | out_of_scope | routed | 0.007 |
| S26 | abstain | What was SpaceX's revenue in fiscal 2024? | - | routed | company_not_found | routed | 0.008 |
| S27 | abstain | How much deferred revenue did Apple have in fiscal 2024? | - | routed | metric_not_supported | routed | 0.007 |
| S28 | abstain | What was Apple's revenue in fiscal 2019? | - | routed | numeric_fact | routed | 0.008 |
| S29 | abstain | What will Apple's revenue be in 2030? | - | routed | future_period | routed | 0.007 |
| S30 | abstain | What was the revenue in fiscal 2024? | - | routed | unsupported | routed | 0.008 |

## Verdicts

- **routed**: 30

## Latency (seconds)

- p50 0.01 · p95 0.01 · max 0.01
