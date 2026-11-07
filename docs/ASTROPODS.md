# Astropods AI integration

## Verified application status — November 7, 2026

Hosted application: https://caseweave.onrender.com/

A browser check exercised sample loading, chronology analysis, exact-version approval, simulated submission, and the resulting audit trail. The Connections dialog reported server-side PayPal credentials present, but no actual authentication, dispute retrieval, or submission was tested. AI was not configured.

![Hosted sample workflow](assets/hosted-demo.jpg)

Render initially displayed its service-waking page; a subsequent reload opened the application. Account credits do not by themselves change a service's selected plan. Confirm availability and cost before changing the plan for judging.

## The two APIs are different

- The Astropods **control-plane API**, at `https://astropods.com/api/v1`, manages accounts, blueprints, and deployments. It uses a CLI/user token or an agent's `ASTRO_AUTHZ_TOKEN` where documented.
- The **AI Gateway** handles model calls. Its container-injected `ASTRO_GATEWAY_URL` and `ASTRO_GATEWAY_API_KEY` are distinct from control-plane credentials.

Never put a CLI access token or `ASTRO_AUTHZ_TOKEN` into CaseWeave's `AI_API_KEY`. Do not export managed credentials from an agent container to Render on the assumption that external use is supported.

## Adapter support implemented

When running within an authorized Astropods container, CaseWeave now reads:

| Injected variable | Application mapping |
| --- | --- |
| `ASTRO_GATEWAY_URL` | API base; adds `/v1` exactly once |
| `ASTRO_GATEWAY_API_KEY` | Bearer credential for inference |
| `MODEL_DEFAULT` | Selected model |

Explicit `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` take precedence over their native equivalents. Environment values are resolved when settings are constructed.

The gateway docs recommend asking for JSON in the prompt and parsing it as the portable approach. `AI_JSON_MODE=prompt` omits `response_format`, while retaining strict JSON/source-ID validation and honest rule-based fallback. Native gateway configuration defaults to this mode. For a provider supporting the parameter, `AI_JSON_MODE=json_object` preserves the existing behavior.

`AI_MAX_TOKENS` caps requested completion tokens (default 1800, allowed 256–4096). This does not bound input-token charges or guarantee total cost. No automatic model retries are made. Check provider/account budgets before public testing.

## Keep the hosted judge experience on Render

The supplied docs establish gateway injection during `ast project`/deployment; they do not establish a self-serve external gateway key for Render.

Supported routes to confirm with Astropods:

1. **External gateway access:** if Astropods explicitly provides a supported external endpoint and credential, configure Render's `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`, and `AI_JSON_MODE=prompt` in its secret settings.
2. **Managed drafting agent:** run the drafting component inside Astropods, retaining gateway credentials there. Render calls its authenticated HTTP interface. Before implementing/deploying that interface, establish the supported machine-to-machine invocation/authentication contract and preserve Astropods deployment access checks. A public unauthenticated model proxy is not an acceptable shortcut.

No drafting bridge has been deployed, and external access is not claimed. Account authentication and the supported invocation details remain prerequisites. The existing Render sample workflow continues to work without inference.

## Recommended initial model

`claude-haiku-4-5` is listed by Astropods as a fast, inexpensive chat model. Compare its citation faithfulness on the sample cases before accepting it for the final demonstration. No particular call count is promised for a $10 credit balance.

## References

- [AI Gateway](https://docs.astropods.com/ai-gateway)
- [Chat completions / portable structured output](https://docs.astropods.com/ai-gateway/chat)
- [Supported models](https://docs.astropods.com/ai-gateway/models)
- [Deployment authorization](https://docs.astropods.com/api-reference/astropods-api/deployments/authorize-deployment)
