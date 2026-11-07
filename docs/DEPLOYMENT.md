# Deployment and secrets

## Render setup

1. Use the README's Deploy to Render link, or open Render → New → Blueprint and select `abdel-saw/caseweave`.
2. Review `render.yaml`: Docker web service, free plan, `/api/health`, generated `ADMIN_TOKEN`, secure cookie, SQLite. Confirm the plan is available to your account before creating the service.
3. Deploy. The container builds React and serves it from FastAPI using Render's `PORT`.
4. Add the PayPal **sandbox** client ID and secret under the service's Environment settings.
5. Add `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` for your chosen provider. Set `AI_JSON_MODE=prompt` for portable prompt-based JSON (recommended by the Astropods gateway docs), or retain `json_object` for a provider supporting that parameter. Check quota before using the public demo. No inference credits are supplied by this repository.
6. Keep `COOKIE_SECURE=true`. Never prefix secrets with `VITE_` or put them in the public repository.
7. Copy the generated `ADMIN_TOKEN` for operator-only use in Connections. Demo visitors should not need it.
8. After deployment, verify `/api/health`, all three sample scenarios, model drafting, and at least one real sandbox API interaction.

If Blueprint deployment is unavailable, create a Web Service from the same repository, choose Docker, and configure the variables above manually.

## Persistence

Default SQLite files reside on the service's ephemeral filesystem. Sessions may reset when the service restarts; no durability is promised. Add a PostgreSQL service only after checking its cost/availability, then set `DATABASE_URL` to its internal URL. The app normalizes Render's `postgres://` URLs to the psycopg driver. New tables are created on startup. This prototype has no database migration system.

## Credit redemption

The official hackathon resource links to:
https://credits-portal-mmdm.onrender.com/claim/paypal-ai-hackathon

The portal may require account verification and sponsor approval. This project does not assume credits have been granted. Keep a demo-only setup until you confirm account charges and credit coverage. Hosting credits do not establish model inference credits.

## Live verification checklist

- [ ] Public hosted URL opens without a developer account.
- [ ] Two browsers receive distinct case IDs and cannot access each other's case exports.
- [ ] Model output is actually returned; UI shows the model name and source references.
- [ ] API/provider failures show a visible fallback notice.
- [ ] PayPal import succeeds with the configured app's eligible sandbox dispute.
- [ ] Current evidence type and action availability are checked before submission.
- [ ] Approved evidence is accepted by the sandbox API; inspect the case in PayPal afterward.
- [ ] No secrets appear in frontend bundles, logs, exports, or source control.
- [ ] Hosted URL remains accessible through judging; verify provider quotas and availability.

No live validation is claimed until these steps are performed with configured services.

## Use Astropods credits

Deploy the included outbound drafting worker using [docs/ASTROPODS.md](ASTROPODS.md). Render needs `AI_TRANSPORT=astropods_queue` and `DRAFTER_SHARED_TOKEN`; it does not need the Astropods gateway key or an agent endpoint. The normal Render Dockerfile remains the application build; `Dockerfile.drafter` is for Astropods only.


## PayPal verification in the hosted application

After Render deploys the latest commit, open **Connections**, enter the service's `ADMIN_TOKEN` (from Render Environment), and click **Test PayPal connection**. The check only authenticates and reads dispute summaries; it does not submit evidence or create a payment/dispute. Never paste the operator token or PayPal secret into an issue, recording, or chat.

- Authentication fails: check sandbox client ID/secret and environment; the result includes a safe upstream status when available.
- Authentication passes, Disputes fails with 403: enable Disputes in the sandbox app features and confirm access to the intended merchant account.
- Both pass, no disputes returned: obtain/create an eligible sandbox dispute for this merchant; the check reads the first ten disputes created in the last 179 days, not an exhaustive account history.
- Disputes are listed: select an ID to fill the import field, then import it. Inspect its actual evidence and available actions. Successful list/import does not establish submission eligibility.

Report the authentication/Disputes results and any HTTP status to continue verification. Evidence submission remains a separate, explicitly approved step. This diagnostic endpoint is protected by both the operator token and the visitor session.

Official references: [Disputes setup](https://developer.paypal.com/platforms/disputes/integrate-disputes/) and [List disputes](https://developer.paypal.com/api/customer-disputes/v1/disputes-list).
