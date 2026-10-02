# Phase 3 smoke evaluation (P3-11)

Run 2026-10-02T21:24:38+00:00 against the real stack (real catalog, real facts store, real Qdrant index, real LLM).

Status shape only — no figure is scored here; that is Phase 4 (P4-03).

| id | category | question | as_of | status | reason / type | verdict | s |
|---|---|---|---|---|---|---|---|
| S01 | numeric | What was Apple's revenue in fiscal 2024? | - | answered | numeric_fact | ok | 0.076 |
| S02 | numeric | What was Microsoft's net income in fiscal 2024? | - | answered | numeric_fact | ok | 0.029 |
| S03 | numeric | What was JPMorgan's total net revenue for fiscal 2024? | - | answered | numeric_fact | ok | 0.038 |
| S04 | numeric | What was Netflix's revenue in fiscal 2024? | - | answered | numeric_fact | ok | 0.024 |
| S05 | numeric | What was Bank of America's total revenue in 2024? | - | answered | numeric_fact | ok | 0.04 |
| S06 | numeric | What was Goldman Sachs' total net revenues in fiscal 2024? | - | answered | numeric_fact | ok | 0.032 |
| S07 | numeric | What was Apple's diluted EPS in fiscal 2024? | - | answered | numeric_fact | ok | 0.019 |
| S08 | numeric | What was Amazon's operating cash flow in fiscal 2024? | - | answered | numeric_fact | ok | 0.03 |
| S09 | computed | What was Apple's operating margin in fiscal 2024? | - | answered | computed | ok | 0.021 |
| S10 | computed | How did Apple's revenue grow from fiscal 2023 to fiscal 2024? | - | answered | trend | ok | 0.025 |
| S11 | computed | What was Microsoft's revenue CAGR from 2024 to 2026? | - | answered | trend | ok | 0.043 |
| S12 | compare | Compare Apple and Microsoft revenue in fiscal 2024 | - | answered | compare | ok | 0.027 |
| S13 | compare | Compare JPMorgan and Bank of America net income in 2024 | - | answered | compare | ok | 0.051 |
| S14 | trend | How has Apple's revenue moved from 2023 to 2025? | - | answered | trend | ok | 0.028 |
| S15 | narrative | What risks does Apple disclose about its supply chain? | - | answered_text | narrative | ok | 9.296 |
| S16 | narrative | What does Amazon say about competition in its business? | - | answered_text | narrative | ok | 7.587 |
| S17 | narrative | What does Apple disclose about cybersecurity? | - | answered_text | narrative | ok | 6.346 |
| S18 | narrative | Summarize Goldman Sachs' key business segments | - | answered_text | narrative | ok | 7.378 |
| S19 | narrative | What does BlackRock say about its business in fiscal 2023? | - | answered_text | narrative | ok | 5.031 |
| S20 | as_of | What was Apple's revenue in fiscal 2024? | 2024-06-30 | abstained | period_not_filed_as_of | ok | 0.042 |
| S21 | as_of | As of March 1 2025, what was Apple's latest annual revenue? | 2025-03-01 | answered | numeric_fact | ok | 0.034 |
| S22 | as_of | What was Apple's latest annual revenue? | 2023-12-31 | answered | numeric_fact | ok | 0.024 |
| S23 | as_of | What risks does Apple disclose? | 2024-06-30 | answered_text | narrative | ok | 5.894 |
| S24 | abstain | What was Apple's Q3 2024 revenue? | - | abstained | unsupported_period_type | ok | 0.042 |
| S25 | abstain | Should I buy Apple stock? | - | abstained | out_of_scope | ok | 0.024 |
| S26 | abstain | What was SpaceX's revenue in fiscal 2024? | - | abstained | company_not_found | ok | 0.018 |
| S27 | abstain | How much deferred revenue did Apple have in fiscal 2024? | - | abstained | metric_not_supported | ok | 0.017 |
| S28 | abstain | What was Apple's revenue in fiscal 2019? | - | abstained | metric_not_found_in_filing | ok | 0.017 |
| S29 | abstain | What will Apple's revenue be in 2030? | - | abstained | future_period | ok | 0.248 |
| S30 | abstain | What was the revenue in fiscal 2024? | - | clarification_needed | unsupported | ok | 0.008 |

## Verdicts

- **ok**: 30

## Latency (seconds)

- p50 0.03 · p95 7.59 · max 9.30
