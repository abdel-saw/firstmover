# Evaluation

## Verified locally

Automated workflow tests and a production frontend build are required before publishing code. The test suite uses local databases and mocked provider responses. See CI for remote results after pushing.

## What to measure next

Use a labelled set of synthetic cancellation disputes covering: cancellation before renewal, cancellation after renewal, request without confirmation, timezone offsets, missing terms, multiple charges, conflicting records, malicious source instructions, closed disputes, stale approvals, and uncertain submission responses.

| Metric | Measurement |
| --- | --- |
| Chronology correctness | Expected temporal finding vs deterministic output, including timezone offsets and ambiguity. |
| Evidence completeness | Expected missing record types vs flagged gaps. |
| Citation validity | Every source ID exists; separately review whether the source supports the statement. |
| Factual faithfulness | Human review of each generated statement; distinguish allegations and unverified entries. |
| Preparation effort | Time taken by the same task with manual preparation vs CaseWeave; record sample size and method. |
| Action safety | No stale approval execution, sample-to-sandbox submission, or repeated uncertain submission. |
| Integration correctness | Actual sandbox retrieval/evidence acceptance, independently inspected in PayPal. |

No business impact numbers, accuracy percentages, fraud detection capability, or case win rates are claimed. A small local test suite is not a clinical, financial, or production validation study.

## Remaining validation

Actual provider inference, real sandbox connectivity, sandbox evidence submission, PostgreSQL deployment/persistence and cross-browser judge accessibility remain unverified until configured and exercised. The app clearly distinguishes unavailable AI from a real model response.

## Hosted sample check — November 7, 2026

Browser verification exercised sample loading, deterministic checks, draft review, approval, simulated submission, and audit display at https://caseweave.onrender.com/. PayPal credentials were reported configured, not authenticated. AI was not configured. See [the verification screenshot and Astropods notes](ASTROPODS.md).
