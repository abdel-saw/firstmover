# FirstMover — submission draft

## Tagline

A reason to go first: AI helps define a conditional group purchase; PayPal captures a filled group or releases holds and pays organizer-funded early participation bonuses.

## Description

Some group purchases never happen because everyone waits for someone else to commit first. FirstMover explores a known economic mechanism, inspired by Alex Tabarrok's dominant assurance contracts: offer eligible early participants a small organizer-funded bonus if the group misses its target.

A merchant creates a private workspace, connects their PayPal Sandbox app from the dashboard and describes a group offer. AI proposes bounded, editable terms and identifies assumptions and unanswered questions. The organizer reviews the exact price, threshold, deadline, early cohort and maximum bonus liability before launch. Participants separately accept those terms and approve PayPal Sandbox authorization checkout.

The organizer settles the result. A filled group requires verified captures. An unfilled group releases its holds before paying early bonuses through Payouts. Every operation is persisted before dispatch and independently read back. Unknown outcomes stay unresolved; recovery can refund verified captures and void remaining holds. The application does not claim atomic multi-payment settlement, segregated reserves, automatic legal enforcement or guaranteed demand improvement.

The repository includes setup instructions for judges to explore both outcomes in local simulation. The public hosted campaign pages provide participant checkout for open sandbox campaigns. Sandbox offers with early bonuses require a successful USD 0.01 Payouts permission test in that merchant’s workspace; participants use distinct personal sandbox payer accounts.

## Tools

PayPal Orders v2 and Payments v2 implement authorization, capture, void, refund and reads. PayPal Payouts implements participation bonuses. Astropods hosts the outbound worker and AI Gateway for structured campaign proposals. Render hosts the API/UI. React/Vite, FastAPI and SQLAlchemy provide the application. OpenAI Codex supported implementation, debugging, tests and documentation; the runtime model is shown after an actual proposal response.

## Deployment targets

- Hosted: https://firstmover.onrender.com/
- Repository: https://github.com/abdel-saw/firstmover
- License: MIT, at repository root.
- Video: **not recorded yet**; must be public on YouTube and under three minutes.

## Verification language to finish before submission

Both PayPal Sandbox settlement branches were observed on the previous deployment. Repeat checkout and settlement checks on the new service and verify live AI proposal generation before submitting the new links. Local tests mock PayPal and model HTTP.

## Credits and novelty

Credit Tabarrok's existing mechanism and acknowledge implementations such as EnsureDone. The proposed contribution is an AI-assisted PayPal Sandbox realization with human-approved terms and inspectable operation state, not invention of refund bonuses. De Bono's provocation technique informed the exploration. No affiliation or "first ever" claim.
