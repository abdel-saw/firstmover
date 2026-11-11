# Run FirstMover on your own infrastructure

Start with the [local walkthrough](#1-run-locally). It exercises both outcomes without the hosted demo, paid credits, API keys, or a PayPal account. To test the PayPal Sandbox integration or AI drafts, follow the optional sections below using accounts you control.

| What you want to verify | What you need |
| --- | --- |
| Campaign creation, both settlement outcomes and the ledger | Local simulation |
| Actual authorizations, captures and releases | One PayPal business sandbox account and REST app, plus two distinct personal sandbox buyer accounts |
| Actual early bonuses | The sandbox accounts above and a successful Payouts check |
| Actual AI proposals | Your own Astropods worker and gateway access, or an OpenAI-compatible model endpoint |
| A hosted copy | Your own Render service and PostgreSQL database, or a Docker host with PostgreSQL |

## 1. Run locally

Install Git, Python 3.12 or later, and Node.js 22 or later with npm. Clone the [public repository](https://github.com/abdel-saw/firstmover).

**Windows PowerShell**

```powershell
git clone https://github.com/abdel-saw/firstmover.git
cd firstmover
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe -m uvicorn firstmover.main:app --app-dir backend --host 127.0.0.1 --port 8000 --env-file .env
```

**macOS or Linux**

```bash
git clone https://github.com/abdel-saw/firstmover.git
cd firstmover
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m uvicorn firstmover.main:app --app-dir backend --host 127.0.0.1 --port 8000 --env-file .env
```

Check [health](http://127.0.0.1:8000/api/health) for `{"status":"ok"}`. Open the [merchant dashboard](http://127.0.0.1:8000/organizer), select **Create a merchant account**, and choose a test email and a password of at least 12 characters. Local SQLite tables are created automatically.

**Try both outcomes**

1. Open **Terms studio**. Use the editable preset. Set group size to **2**, price to **$10**, early places to **1**, and bonus to **$1**. Choose **Local simulation**, review the terms, and click **Approve & launch**.
2. In the campaign desk, click **Add demo participant** twice, then **Approve & fund group**. Check the two simulated captures in the settlement ledger.
3. Launch a second local simulation with the same terms. Add one participant and click **Advance deadline & settle**. Check the simulated release and bonus.
4. Open the campaign audit trail. The public catalog lists open PayPal Sandbox offers, while local simulations are visible in the merchant dashboard.

For a Docker-only evaluation, clone the repository, copy `.env.example` to `.env`, then run the commands below. The default SQLite database inside this disposable container lasts for that run. For saved sandbox activity, use the Python setup above or point Docker to persistent PostgreSQL.

```bash
docker build -t firstmover .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env firstmover
```

## 2. Deploy your own hosted copy

1. Fork the repository into your own GitHub account. On [Render](https://dashboard.render.com/), create a PostgreSQL database. Use the same region for the app and the database.
2. Select **New → Blueprint**, connect your fork and select its `main` branch. The root `render.yaml` defines the Docker service and its health check.
3. Set `DATABASE_URL` to the database's **Internal Database URL**. Provide `DRAFTER_SHARED_TOKEN`, a random value of at least 32 characters. Use the same value in Astropods when deploying the worker. Render generates `MERCHANT_ENCRYPTION_KEY` and sets `COOKIE_SECURE=true`. Retain the encryption key with the database so saved PayPal credentials can still be decrypted.
4. Open `/api/health` on your assigned HTTPS URL, then create a merchant account at `/organizer`. The local simulation walkthrough also works on your hosted copy. Record the actual HTTPS origin for the AI worker.
5. For a Docker host outside Render, use the same Docker build with your own persistent PostgreSQL `DATABASE_URL` and HTTPS reverse proxy. Set `COOKIE_SECURE=true`, preserve the HTTPS scheme and host in the proxy headers, and supply your own stable `MERCHANT_ENCRYPTION_KEY`. Restrict direct access to the app port to the proxy. Use your host's secret store for credentials.

Render's free PostgreSQL plan expires after 30 days. If the deployment must outlast that, use a database with retention covering the evaluation period. [Render PostgreSQL setup](https://render.com/docs/postgresql-creating-connecting) · [Render free-plan limits](https://render.com/docs/free#free-postgres)

## 3. Use your own PayPal Sandbox accounts

1. In the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/), choose **Sandbox** and create a REST app linked to a business sandbox account. Create two distinct personal sandbox buyer accounts under **Testing Tools → Sandbox Accounts**. [PayPal REST setup](https://developer.paypal.com/api/rest/)
2. When running locally, generate a stable encryption key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, set `MERCHANT_ENCRYPTION_KEY` in `.env`, and restart the app. On Render, use the generated key.
3. Sign into `/organizer`, click **Connect PayPal**, and enter the app's Sandbox Client ID and Secret. The dashboard verifies the connection before saving it.
4. For offers with early bonuses, use **Optional Payouts check** to send $0.01 in virtual funds to a personal sandbox buyer email. Wait for verified `SUCCESS`. If the status is pending, reconcile the same test. A sandbox offer with bonuses disabled only requires the connected app.
5. Launch a **PayPal sandbox** campaign with a group size of two. Use a 10-minute window for checkout. Open the public campaign link in two separate browser profiles, agree to the displayed terms, and approve with a different personal sandbox buyer in each profile.
6. In the merchant dashboard, verify both authorized holds and select **Approve & fund group**. Verify both completed captures in the settlement ledger.
7. Launch a second offer with one early place and a bonus. Add one eligible buyer. Let the real deadline pass, then select **Settle unfilled group**. Verify the release and successful bonus payout. To keep a rehearsal short, choose the shortest window that lets you finish buyer approval comfortably.

Only Sandbox virtual funds are supported. If settlement shows a pending or review state, use **Resume / reconcile** and inspect the Sandbox transaction before taking another action. Resolve any remaining sandbox holds after testing.

## 4. Enable AI drafting

**Astropods.** Follow the [Astropods worker guide](ASTROPODS.md) from your fork. Set `FIRSTMOVER_URL` to your own hosted origin and deploy the worker with your own gateway access. Match the shared token across both services. Use a new brief in **Terms studio → Draft terms with AI** to check that a model name and editable proposal appear.

**Direct provider.** Set these values in `.env` locally, or in the hosting service's environment, and restart the app:

```dotenv
AI_TRANSPORT=direct
AI_BASE_URL=https://your-provider.example/v1
AI_API_KEY=your-own-provider-key
AI_MODEL=your-provider-model-id
AI_JSON_MODE=prompt
```

Use an endpoint that implements OpenAI-compatible chat completions. The app appends `/chat/completions` to the base URL. The form's preset remains editable when AI access is unavailable.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Local page says the API is ready | Run `npm ci --prefix frontend` and `npm run build --prefix frontend` from the repository root, then restart the server. |
| Merchant login works but the PayPal form is unavailable | Set a persistent `MERCHANT_ENCRYPTION_KEY` of at least 32 characters, restart or redeploy, and reopen the dashboard. |
| The hosted public catalog is empty | Create a sandbox campaign or run the local simulations from the merchant dashboard. |
| AI shows a timeout or no model result | Check the worker's status, actual Render origin, matching shared token and gateway quota. For direct mode, check the model ID and API URL. |
| A PayPal payment step is pending | Read the merchant ledger, then use the reconciliation action and check the PayPal Sandbox account for the provider state. |

Run the automated checks with `python -m pytest -q` in the prepared virtual environment and `npm run build --prefix frontend`. The tests exercise the PayPal and AI flows with mocked provider HTTP.
