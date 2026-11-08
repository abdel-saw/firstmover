# FirstMover evaluation

## Technical evidence

Run the route-level test suite, frontend build, and isolated worker image import check. Test both local-simulation branches and provider-mocked sandbox branches. Keep all live verification separate from mocks.

Required cases: exact-term consent; invalid amounts; early eligibility; expiry; duplicate payer accounts; missing Payouts permission; pending/wrong-recipient payouts; repeated settlement; lost capture acknowledgment; incorrect capture currency/amount; partial settlement recovery; unresolved authorization absent from a read; participant privacy and operator permissions; real AI queue transport with model HTTP mocked.

## Live account checks

Use the checklist in [FIRSTMOVER.md](FIRSTMOVER.md). Record which provider operations were actually observed and their independently retrieved states. A successful OAuth check, a created order, or a successful POST alone does not prove settlement or beneficiary receipt.

## Economic hypothesis — unmeasured

The hypothesis is that an organizer-funded failure bonus can encourage early participation. This implementation has not tested that hypothesis with real people. Do not report increased conversion, reduced hesitation or improved campaign success as measured outcomes.

A later consent-based study could compare identical group offers with and without early bonuses, measuring actual authorization completion, time to first commitment, completed group formation, organizer costs and participant understanding. Report sample sizes, uncertainty, abandoned checkouts and failures, not only successful examples. Avoid treating synthetic AI participants as evidence of human behavior.
