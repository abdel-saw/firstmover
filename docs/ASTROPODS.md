# Deploy the FirstMover drafting worker

The worker claims campaign proposals from the Render app through an authenticated outbound queue. It sends the merchant's brief to the Astropods AI Gateway and returns a structured draft for review. PayPal credentials and participant payment data stay outside that queue.

## Configure Render and Astropods

Set `AI_TRANSPORT=astropods_queue` and `DRAFTER_SHARED_TOKEN` in the FirstMover Render service. Use a random token of at least 32 characters and store the same value in the Astropods vault under `DRAFTER_SHARED_TOKEN`. Keep the actual value in each platform's secret settings.

From the FirstMover repository, run:

```powershell
ast login
# Create the secret only if it is missing from the vault.
ast secrets create DRAFTER_SHARED_TOKEN
ast blueprint push
ast blueprint deploy firstmover-drafter --var "DRAFTER_SHARED_TOKEN=@" --var "FIRSTMOVER_URL=https://firstmover-dn0t.onrender.com" --wait
```

For an existing deployment, push the blueprint and redeploy the same agent:

```powershell
ast agent redeploy --name firstmover-drafter --var "DRAFTER_SHARED_TOKEN=@" --var "FIRSTMOVER_URL=https://firstmover-dn0t.onrender.com" --wait
```

Use your actual Render origin if it differs. Select `claude-haiku-4-5` or `claude-sonnet-4-6` from the blueprint's gateway models. Astropods injects the gateway credentials inside the worker.

## Verify

1. Confirm the agent is active. Its `/health` route reports configuration readiness.
2. Sign in at `/organizer` and open **Terms studio**.
3. Draft terms from a new brief. Confirm that editable terms and the model name appear.
4. If the proposal fails, check the worker status and logs, gateway quota, shared token, and Render URL. The dashboard also supports manually edited terms.

The worker limits inference attempts per minute and per process. An invalid or truncated response can receive one correction request within those limits. A proposal still requires merchant approval before a campaign can be launched.
