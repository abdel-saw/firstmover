# FirstMover verification and demo plan

## Ready to test

The source contains a working local-simulation campaign experience, bounded AI campaign proposals via the Astropods queue, and sandbox payment adapters. The UI preserves the distinction between simulation, model output, authorization, captured payment, voided authorization, pending payout and verified payout success.

## Verify the new deployment

- Deploy the same revision to the new Render service and `firstmover-drafter` worker.
- Register two merchant workspaces and connect separate PayPal Sandbox apps. Confirm each sees only its own campaigns and Payouts status.
- Repeat the USD 0.01 Payouts permission test with a personal sandbox recipient.
- Verify one buyer approval → authorization → independent authorization GET.
- Verify a two-person filled campaign → two captures → independent capture GETs.
- Verify an unfilled one-minute campaign → authorization void → independent GET → bonus payout → independently matched SUCCESS.
- Verify actual AI proposal generation, not a preset or stored model fixture.
- Review and, where applicable, resolve all sandbox holds after the recording.

Both sandbox settlement branches were observed on the prior deployment. Repeat them on the new service and verify an AI proposal there.

## Financial behavior

An operation is persisted before dispatch. Idempotency identifiers are stable. An existing uncertain operation is only read back, never blindly POSTed again. A successful POST is insufficient: the exact provider ID, amount, currency and relevant account/binding must match. Payouts require both the batch identity and the single recipient/item identity. Unknown captures block recovery until reconciled. Definite pre-acceptance rejections are distinguished from uncertain outcomes.

Group funding is not atomic. A failed partial settlement is visible as needing review; recovery separately refunds captured commitments and voids remaining holds. That recovery is not treated as group demand failure and earns no bonus. Unfilled groups release all holds before sending any early bonuses. Failure to pay a bonus remains unresolved, never labeled paid.

The campaign's structured terms, digest and deadline are frozen on launch. Participants explicitly accept them. Sandbox payer identity is obtained from PayPal; one payer account can authorize at most once per campaign. Demo participants are synthetic and intentionally bypass account checks without calling PayPal. Only demo mode can advance its own deadline.

Early eligibility is computed by successful authorization completion time, not by typing a name or opening checkout. The eligible list is frozen when the organizer closes the campaign. The organizer initiates settlement, so a clock reaching zero does not imply that PayPal already executed anything.

The advertised maximum bonus liability excludes PayPal fees. It is an internal calculation, not proof of a funded or segregated reserve. An API success is not a guarantee against later reversals or funding-provider delays.

## Suggested video, under three minutes

- 0:00–0:20: explain the coordination problem and organizer-funded failure bonus. Credit the existing assurance-contract idea.
- 0:20–0:45: generate a real AI proposal, inspect assumptions, edit and approve structured terms.
- 0:45–1:25: show the actual verified sandbox failure branch: buyer-approved hold, missed threshold, independently read void, bonus SUCCESS. Hide operator tokens, account emails and secrets.
- 1:25–1:55: show the verified filled branch and its capture receipts. Only show real provider IDs if the live test actually completed.
- 1:55–2:20: inspect the operation audit and explain unknown/partial settlement handling.
- 2:20–2:40: show the hosted URL, repository, tools and prototype limits. Do not claim economic improvements from this demo.

If any branch uses local simulation or saved results, label it visibly and state what the recording actually establishes. No third-party music or media is needed.

## Research and feasibility references

- https://marginalrevolution.com/marginalrevolution/2005/05/assurance_contr.html
- https://marginalrevolution.com/marginalrevolution/2024/01/worlds-first-dominant-assurance-contract-platform.html
- https://developer.paypal.com/api/orders/v2/orders-authorize
- https://developer.paypal.com/api/payments/v2/authorizations-capture
- https://developer.paypal.com/api/payments/v2/authorizations-void
- https://developer.paypal.com/api/payouts
