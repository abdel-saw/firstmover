# CaseWeave — submission draft

## Pitch

CaseWeave connects scattered merchant records into traceable dispute evidence. Starting with cancellation-related recurring billing, it compares event timestamps, exposes missing records, and prepares a response that a merchant can inspect and approve before submitting to PayPal.

## Problem

A cancellation request, completed cancellation, payment, and refund are different events. They often live in different records. An incomplete or contradictory response creates additional work for merchants and case reviewers.

## What it does

- Presents source records on a chronological timeline.
- Distinguishes customer assertions, merchant-supplied records, synthetic fixtures, and imported sandbox API facts.
- Checks for missing cancellation confirmation, payment records, and agreement terms.
- Uses an optional language model to prepare source-linked observations and an editable response.
- Requires approval of the exact draft/evidence version.
- Exports a case package and submits supported note evidence to eligible PayPal sandbox disputes.
- Records actions and uncertain submission results in the audit trail.

## Value hypothesis

Better organized evidence may reduce preparation time and repeated requests for information. We evaluate completeness, citation accuracy, and workflow correctness. No reduction in actual PayPal support cost, fraud losses, or case resolution time has been established.

## Tools disclosure

| Tool | Use |
| --- | --- |
| PayPal sandbox Disputes API | Authenticate, retrieve dispute details, and submit merchant-approved OTHER note evidence. |
| Python / FastAPI / Pydantic | API, validation, chronology checks, and workflow controls. |
| SQLAlchemy / SQLite or PostgreSQL | Session-scoped case persistence and submission state. |
| React / Vite / Lucide | Merchant UI and original visual design. |
| Configured model provider | Source-linked drafting using an OpenAI-compatible chat-completion endpoint. **Replace this row with the actual provider/model and observed usage before submission.** |
| Render | Intended Docker hosting. **Confirm deployment and URL before submission.** |
| ChatGPT / Codex | Development assistance, implementation, and test authoring. |

No AG Grid, Bryntum, Elastic, or other sponsor integration is claimed in this version.

## Links to fill in after verification

- Repository: https://github.com/abdel-saw/caseweave
- Hosted application: https://caseweave.onrender.com/ (sample workflow verified; model and sandbox connectivity still require validation)
- Public YouTube demo: **pending recording/upload**

## Honest demonstration boundaries

Synthetic sample workflows are labelled; their submission receipts are simulations. Sandbox import and submission are separate operator-only operations. State clearly in the final entry which actual API interactions were verified and which model performed inference. Do not submit an AI claim based solely on rule-based fallback output.
