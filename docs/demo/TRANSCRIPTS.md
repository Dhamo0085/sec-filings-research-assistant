# Demo transcripts — plan B

Real output from the eight `docs/DEMO.md` queries, captured 2026-10-05 by
`python query.py <question>` against the live store (39 collections, 37,882
points). **If the live demo fails, read from here**: these are the real
answers, not mock-ups, and saying so is better than a dead screen.

Capture command, for any single one of them:

```bash
python query.py "What was Netflix's revenue in fiscal 2024?"
```


---

## D1 — numeric — a filer that reports in thousands

```bash
python query.py "What was Netflix's revenue in fiscal 2024?"
```

```text
====================================================================
QUERY : What was Netflix's revenue in fiscal 2024?
STATUS: answered
====================================================================

NETFLIX INC's total net revenue for fiscal 2024 (year ended 2024-12-31) was $39.00 billion ($39,000,966,000). [1]

Definition used: Total net revenue = us-gaap:Revenues, fiscal year ended 2024-12-31

--------------------------------------------------------------------
SOURCES:
  [1] NETFLIX INC FY2024 — revenue (us-gaap:Revenues)
       0001065280-25-000044, filed 2025-01-27
====================================================================
```

---

## D2 — numeric — a bank's own revenue line

```bash
python query.py "What was JPMorgan's total net revenue for fiscal 2024?"
```

```text
====================================================================
QUERY : What was JPMorgan's total net revenue for fiscal 2024?
STATUS: answered
====================================================================

JPMorgan Chase & Co.'s total net revenue for fiscal 2024 (year ended 2024-12-31) was $177.56 billion ($177,556,000,000). [1]

Definition used: Total net revenue = us-gaap:Revenues, fiscal year ended 2024-12-31

--------------------------------------------------------------------
SOURCES:
  [1] JPMorgan Chase & Co. FY2024 — revenue (us-gaap:Revenues)
       0000019617-25-000270, filed 2025-02-14
====================================================================
```

---

## D3 — computed — deterministic calculator

```bash
python query.py "What was Apple's operating margin in fiscal 2024?"
```

```text
====================================================================
QUERY : What was Apple's operating margin in fiscal 2024?
STATUS: answered
====================================================================

Apple Inc.'s operating income margin for fiscal 2024 was 31.51%. Computed as 123216000000 / 391035000000 x 100 from fiscal 2024 (year ended 2024-09-28) operating income $123.22 billion ($123,216,000,000); fiscal 2024 (year ended 2024-09-28) total net revenue $391.04 billion ($391,035,000,000). [1] [2]

Definition used: Operating income = us-gaap:OperatingIncomeLoss, fiscal year ended 2024-09-28; Total net revenue = us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, fiscal year ended 2024-09-28

--------------------------------------------------------------------
SOURCES:
  [1] Apple Inc. FY2024 — operating_income (us-gaap:OperatingIncomeLoss)
       0000320193-24-000123, filed 2024-11-01
  [2] Apple Inc. FY2024 — revenue (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax)
       0000320193-24-000123, filed 2024-11-01
====================================================================
```

---

## D4 — compare — two companies, each figure attributed

```bash
python query.py "Compare Apple and Microsoft revenue in fiscal 2024"
```

```text
====================================================================
QUERY : Compare Apple and Microsoft revenue in fiscal 2024
STATUS: answered
====================================================================

Total net revenue, as each company reports it:
- Apple Inc.: $391.04 billion ($391,035,000,000) for fiscal 2024 (year ended 2024-09-28) [1]
- Microsoft Corporation: $245.12 billion ($245,122,000,000) for fiscal 2024 (year ended 2024-06-30) [2]

Apple Inc. reported $145.91 billion ($145,913,000,000) more than Microsoft Corporation, 59.53% higher. Note the fiscal years end on different dates, so the periods are not identical.

Definition used: Total net revenue = us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, fiscal year ended 2024-09-28; Total net revenue = us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, fiscal year ended 2024-06-30

--------------------------------------------------------------------
SOURCES:
  [1] Apple Inc. FY2024 — revenue (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax)
       0000320193-24-000123, filed 2024-11-01
  [2] Microsoft Corporation FY2024 — revenue (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax)
       0000950170-24-087843, filed 2024-07-30
====================================================================
```

---

## D5 — point in time — the filing was not public yet

```bash
python query.py --as-of 2024-06-30 "What was Apple's revenue in fiscal 2024?"
```

```text
====================================================================
QUERY : What was Apple's revenue in fiscal 2024?
STATUS: abstained (period_not_filed_as_of)
AS OF : 2024-06-30
====================================================================

As of 2024-06-30, Apple Inc had not yet filed its annual report for fiscal 2024 — that filing became public on 2024-11-01. Answering from it would be using information that did not exist on the date you asked about. I do have fiscal 2021 to 2023.

====================================================================
```

---

## D6 — abstention — no such filer

```bash
python query.py "What was SpaceX's revenue in fiscal 2024?"
```

```text
====================================================================
QUERY : What was SpaceX's revenue in fiscal 2024?
STATUS: abstained (company_not_found)
====================================================================

I couldn't find SpaceX in SEC's company registry. It may be private, may file under a different name, or may not file with the SEC at all. I can only answer from filed US annual reports.

====================================================================
```

---

## D7 — abstention — out of scope, not investment advice

```bash
python query.py "Should I buy Apple stock?"
```

```text
====================================================================
QUERY : Should I buy Apple stock?
STATUS: abstained (out_of_scope)
====================================================================

That's outside what I do. I answer questions about what US companies reported in their annual filings — not investment advice, price or market data, forecasts, or recommendations.

====================================================================
```

---

## D8 — narrative — retrieval + the only model call in this table

```bash
python query.py "What risks does Apple disclose about its supply chain?"
```

```text
====================================================================
QUERY : What risks does Apple disclose about its supply chain?
STATUS: answered_text
====================================================================

Apple discloses that any failure of manufacturing or logistics partners can negatively impact component or finished goods costs and supply, and that disruptions can occur due to natural and man-made disasters, information technology system failures, commercial disputes, economic, business, labor, environmental, public health or political issues, trade and international disputes, geopolitical tensions, or conflict [1]. The company has invested in manufacturing process equipment held at outsourcing partners and made prepayments for long-term supply agreements, but severe financial problems or business disruptions at these partners can disrupt supply and negatively impact the recoverability of that equipment or prepayments [1]. Changes or additions to the supply chain require considerable time and resources, exposing the company to additional regulatory and operational risks, and future operating results depend on obtaining components in sufficient quantities on commercially reasonable terms [1]. Obtaining certain components from single or limited sources creates significant supply and pricing risks, and many components are subject to industry-wide shortages and commodity pricing fluctuations, such as past semiconductor industry high demand and shortages [1]. Component suppliers may also suffer from poor financial conditions leading to business failure or consolidation [1]. New products often use custom components from only one source, and initial capacity constraints may exist until supplier yields mature or capacities increase, or if suppliers decide to concentrate on common components instead of custom ones [1]. Delays or constraints in component supply or shipments from outsourcing partners can adversely affect business, results of operations, and financial condition, as can the time required to obtain sufficient quantities from the source or an alternative source [1].

--------------------------------------------------------------------
SOURCES:
  [1] Apple Inc. FY2024 — Item 1A: Risk Factors
       0000320193-24-000123, filed 2024-11-01
====================================================================
```

*This is the only one of the eight that calls a model.*
