# FirstMover — submission draft

## Tagline

A reason to go first: AI helps define a conditional group purchase; PayPal captures a filled group or releases holds and pays organizer-funded early participation bonuses.

## Description

Some group purchases never happen because everyone waits for someone else to commit first. FirstMover explores a known economic mechanism, inspired by Alex Tabarrok's dominant assurance contracts: offer eligible early participants a small organizer-funded bonus if the group misses its target.

An organizer describes a workshop. AI proposes bounded, editable terms and identifies assumptions and unanswered questions. The organizer reviews the exact price, threshold, deadline, early cohort and maximum bonus liability before launch. Participants separately accept those terms and approve PayPal Sandbox authorization checkout.

The organizer settles the result. A filled group requires verified captures. An unfilled group releases its holds before paying early bonuses through Payouts. Every operation is persisted before dispatch and independently read back. Unknown outcomes stay unresolved; recovery can refund verified captures and void remaining holds. The application does not claim atomic multi-payment settlement, segregated reserves, automatic legal enforcement or guaranteed demand improvement.

The hosted demo also includes a clearly labeled local simulation, so judges can explore both outcomes without credentials. Actual sandbox mode requires the operator's successful USD 0.01 Payouts permission test; participants use distinct personal sandbox payer accounts.

## Tools

PayPal Orders v2 and Payments v2 implement authorization, capture, void, refund and reads. PayPal Payouts implements participation bonuses. Astropods hosts the outbound worker and AI Gateway for structured campaign proposals. Render hosts the API/UI. React/Vite, FastAPI and SQLAlchemy provide the application. OpenAI Codex supported implementation, debugging, tests and documentation; the runtime model is shown after an actual proposal response.

## Links

- Hosted: https://caseweave.onrender.com/
- Repository: https://github.com/abdel-saw/caseweave
- License: MIT, at repository root.
- Video: **not recorded yet**; must be public on YouTube and under three minutes.

## Verification language to finish before submission

Update this section only after observing real results. At implementation time, local tests use mocked PayPal/model HTTP. Actual FirstMover payout, hold, capture, void, failure bonus and new AI-proposal generation remain to be verified. Do not describe the older CaseWeave OAuth/Disputes checks as proof of those capabilities. Keep any unverified branches explicitly labeled.

## Credits and novelty

Credit Tabarrok's existing mechanism and acknowledge implementations such as EnsureDone. The proposed contribution is an AI-assisted PayPal Sandbox realization with human-approved terms and inspectable operation state, not invention of refund bonuses. De Bono's provocation technique informed the exploration. No affiliation or "first ever" claim.
