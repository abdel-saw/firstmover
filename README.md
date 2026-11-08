# FirstMover

**A reason to go first.** An experimental PayPal + AI coordination mechanism: participants authorize a conditional group purchase; if the group fills, the organizer captures the commitments. If the group misses its target, authorizations are voided and eligible early participants receive organizer-funded bonuses through PayPal Payouts.

Built for the PayPal AI Hackathon. The mechanism is inspired by **Alex Tabarrok's dominant assurance contracts**, not invented here. De Bono's provocation technique helped frame the question: *what if a purchase that never happens sometimes pays the buyer?*

**Current verification:** demo settlement, API safety paths, and the AI worker transport are exercised locally. PayPal HTTP and model responses are mocked in those tests. FirstMover's actual Payouts, authorization, capture, void, and bonus flows have not yet been verified against our account. The earlier CaseWeave app verified authentication/Disputes read access and real AI drafting; those do not establish FirstMover's payment capabilities. No improved conversion rate or production readiness is claimed.

Hosted application: https://caseweave.onrender.com/ (existing Render hostname retained). The prior project remains available at `/?app=caseweave`; its documentation is [CASEWEAVE.md](CASEWEAVE.md).

## Why this is more than checkout

A group can remain stuck because each person will participate only if others do. The organizer offers a small, capped failure bonus to early participants. PayPal's separate financial states implement the experiment: approve → authorize → capture **or** void → payout. An authorization is not a completed payment, and releasing a hold is not paying a bonus.

This prototype is **not** a general escrow service, investment product, guaranteed funding mechanism, or segregated bonus reserve. The application coordinates ordinary PayPal APIs; PayPal does not enforce the campaign condition or provide cross-payment atomicity. Bonuses come from the organizer. Any production use requires PayPal policy and legal review.

## Judge walkthrough

No credentials are needed for local simulation:

1. Open **Terms studio**. Edit the printmaking preset, or use **Draft terms with AI** if configured.
2. Review the structured terms, maximum bonus liability, and execution mode. Select **Local simulation**, approve, and launch.
3. Add three demo participants; click **Approve & fund group**. Inspect the simulated capture receipts.
4. Launch another campaign and add one participant. Click **Advance deadline & settle**. The demo advances its own deadline, releases the simulated hold and pays a simulated early bonus.
5. Inspect the audit trail and export the JSON record.

Every simulated receipt is labeled `SIMULATED`. The deadline advance is available only in demo mode. Real sandbox payments cannot be fast-forwarded. A manual preset is never labeled AI-generated.

## Run locally

Python 3.12+ and Node.js 22+:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
python -m uvicorn caseweave.main:app --app-dir backend --host 127.0.0.1 --port 8000 --env-file .env
```

First copy `.env.example` to `.env` and leave secrets empty for demo mode. Open http://127.0.0.1:8000/. Build the frontend again after UI edits. `COOKIE_SECURE=false` is for local HTTP; Render uses `true`.

Or use Docker:

```bash
docker build -t firstmover .
docker run --rm -p 8000:8000 --env-file .env firstmover
```

## PayPal sandbox verification

1. Keep the existing Render sandbox credentials and `ADMIN_TOKEN`. No production endpoint exists in the adapter.
2. In **Sandbox setup**, enter the operator token. It remains in page memory; participants do not need it.
3. Enter an actual **personal sandbox account email**, approve the USD 0.01 virtual-funds test, and click **Send / reconcile test payout**.
4. Only an independently read `SUCCESS` for the correct recipient, amount, currency, batch and item unlocks sandbox campaign creation. Pending/unclaimed/failed results do not pass. Enable Payouts in the developer app if access is denied. After a definite pre-acceptance rejection, explicitly repeating the approved test starts a new attempt; pending or uncertain payouts only reconcile the original attempt.
5. Create a **PayPal sandbox** campaign. For a short demo, use a one-minute decision window and the minimum group size of two. The organizer acknowledges the published bonus liability; fees are additional, and no reserve is segregated or guaranteed.
6. Share the campaign link. Each participant approves the exact terms, uses a distinct personal sandbox PayPal account, and approves PayPal Checkout. The backend separately authorizes and verifies the hold. A display name or successful redirect is not a funded commitment.
7. The organizer may settle a full group early. An unfilled sandbox group may be settled only after its real deadline. The organizer must initiate settlement; no background scheduler is promised.
8. Inspect receipts and the sandbox account independently. A pending/unknown result stops the workflow. **Resume / reconcile** never re-dispatches an uncertain write. A definite rejected capture may be recovered after inspection. **Cancel & recover** refunds verified captures and voids remaining holds; technical recovery does not earn group-failure bonuses.

Sandbox MVP limits: one organizer merchant, USD, 2–6 participants, USD 1–20 per commitment, up to three early participants, USD 0.01–2 bonus each, 1–120 minute decision windows. A maximum of 12 checkout attempts and 12 campaigns per browser session limits demo use. Duplicate payer accounts are prevented per campaign; this does not prove unique real-world people or prevent coordinated bonus farming.

### Persistence matters

The default SQLite file is suitable for local experiments. Render's default filesystem is ephemeral: redeploys/restarts can erase campaigns and operation history while sandbox authorizations still exist externally. **Use a retained database (PostgreSQL or a persistent disk) before relying on saved sandbox workflows across deployments.** Duplicate-write protection depends on retained operation records. Never use this prototype with real money. If records are lost, inspect the merchant's sandbox account and resolve holds there; do not recreate a campaign as a recovery method.

## AI: proposal, not permission

The existing Astropods worker now handles campaign proposals in addition to archived evidence drafts. Redeploy it after pulling this version:

```powershell
git pull --ff-only
ast blueprint push
ast agent redeploy --name caseweave-drafter --var "DRAFTER_SHARED_TOKEN=@" --wait
```

Render must deploy the same revision. The existing `AI_TRANSPORT=astropods_queue` and shared service token stay in place. No managed gateway credential is copied to Render.

The worker sends only the organizer's short brief to the configured model. A strict schema bounds the title, description, participant threshold, ticket amount, early cohort, bonus, duration, questions and explanation. The backend revalidates the output. A failed model response is displayed as an error; a manual preset remains available, labeled as such. Failed/expired proposals can be explicitly retried after a configuration fix, up to three attempts per unchanged brief. Completed proposals are cached. No generation is automatically retried. AI cannot launch a campaign, approve terms, choose a payout recipient, or execute a payment. Code calculates target amounts and maximum bonus liability in integer cents.

The per-process gateway limits remain six attempts/minute and 100 attempts/process by default. These are not a dollar cap; watch Astropods usage and set platform spending limits. See [Astropods deployment](docs/ASTROPODS.md).

## Configuration

| Variable | Use |
| --- | --- |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | Organizer merchant's sandbox app. The JS SDK's client ID is public; the secret stays on the server. |
| `ADMIN_TOKEN` | Operator-only sandbox campaign creation, payout probe and settlement. |
| `DATABASE_URL` | SQLite locally or durable PostgreSQL. Existing `postgres://` Render URLs are supported. |
| `COOKIE_SECURE` | `true` for HTTPS, `false` only for local HTTP. |
| `AI_TRANSPORT` | `astropods_queue` to reuse the outbound worker, or `direct` for a server-side model provider. |
| `DRAFTER_SHARED_TOKEN` | Shared private worker token, at least 32 characters. |
| `DRAFTER_WAIT_SECONDS` | Proposal queue wait, default 55 seconds. |
| `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` | Optional direct OpenAI-compatible model configuration. |
| `AI_JSON_MODE`, `AI_MAX_TOKENS` | Prompt JSON or JSON mode; bounded completion tokens. |

No extra environment variables are needed for FirstMover. Never use frontend build variables for PayPal secrets or operator tokens.

## Tests

```bash
python -m pytest -q
npm run build --prefix frontend
```

New route-level tests use the real HTTP adapter with only PayPal's network boundary mocked. They exercise both settlement branches, exact-term consent, session ownership, payout identity and pending status, duplicate payer accounts, deadlines, duplicate operation prevention, lost capture acknowledgments, incorrect amounts, and partial settlement recovery. A worker/queue/model round trip covers AI proposals with model HTTP mocked. Archived CaseWeave tests remain in the suite.

These tests do not establish production security, economic effectiveness, real beneficiary receipt, or live PayPal account permissions.

## Tools and credits

- **PayPal:** Orders v2 authorization checkout; Payments v2 authorization/capture/void/refund reads and writes; Payouts v1 participation bonuses. Sandbox only.
- **Astropods:** hosted outbound worker and managed AI Gateway for structured campaign proposals.
- **Render:** hosted application and API; retained database recommended.
- **React, Vite, FastAPI, SQLAlchemy:** UI, API and durable operation state.
- **OpenAI Codex:** implementation, debugging, tests and documentation; not represented as the runtime proposal model. The actual runtime model is displayed after generation.
- **Alex Tabarrok:** [dominant assurance contracts](https://marginalrevolution.com/marginalrevolution/2005/05/assurance_contr.html). The economics and refund-bonus concept are prior work. We make no "first ever" claim; other implementations include EnsureDone.
- **Edward de Bono:** provocation/movement informed ideation; no affiliation implied.

License: [MIT](LICENSE). Lucide icons retain their package's ISC license. FirstMover is an independent hackathon experiment, not an official PayPal product.
