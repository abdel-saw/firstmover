"""Deterministic chronology checks, with optional source-constrained LLM drafting."""
import json
from datetime import datetime
import httpx
from .drafting import provider_draft, DraftFailure


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone is required")
    return parsed


def check_case(case):
    evidence = case["evidence"]
    findings, missing = [], []
    groups = {kind: [e for e in evidence if e["kind"] == kind] for kind in ("request", "cancellation", "charge", "agreement")}
    for kind, label in (("cancellation", "Cancellation confirmation with effective timestamp"), ("charge", "Payment record with timestamp"), ("agreement", "Applicable subscription agreement")):
        if not groups[kind]:
            missing.append(label)
    if groups["cancellation"] and groups["charge"]:
        if len(groups["cancellation"]) != 1 or len(groups["charge"]) != 1:
            findings.append({"severity":"warning", "text":"Multiple cancellation or payment records need manual reconciliation before comparing timestamps.", "sources":[e["id"] for e in groups["cancellation"] + groups["charge"]]})
        else:
            cancel, charge = groups["cancellation"][0], groups["charge"][0]
            before = timestamp(cancel["occurred_at"]) < timestamp(charge["occurred_at"])
            findings.append({"severity":"warning" if before else "info", "text": "Recorded charge follows the recorded cancellation. Verify effective cancellation terms before deciding a remedy." if before else "Recorded charge is at or before the recorded cancellation. Timing alone does not settle refund eligibility.", "sources":[cancel["id"],charge["id"]]})
    if groups["request"] and not groups["cancellation"]:
        findings.append({"severity":"warning", "text":"A customer request or assertion is present, but it does not establish a completed cancellation.", "sources":[e["id"] for e in groups["request"]]})
    if any(e["provenance"] == "merchant" for e in evidence):
        findings.append({"severity":"info", "text":"Merchant-supplied records and timestamps have not been independently verified.", "sources":[e["id"] for e in evidence if e["provenance"] == "merchant"]})
    if case["reason"] != "CANCELED_RECURRING_BILLING":
        missing.append("Specialist review: this MVP's chronology checks target cancellation-related billing")
    return {"findings":findings, "missing":missing, "coverage":round(100 * sum(bool(groups[k]) for k in groups) / 4), "recommendation":"Request missing evidence before submitting a response." if missing else "Review the chronology, applicable terms, and requested PayPal evidence before submitting.", "engine":"rules", "model":None}


def rules_draft(case, result):
    lines=["Merchant evidence summary. This is not a determination of the dispute.", f"Reported reason: {case['reason']}."]
    for e in sorted(case["evidence"], key=lambda e: timestamp(e["occurred_at"])):
        lines.append(f"[{e['id']}] {e['occurred_at']} — {e['title']} ({e['provenance']}): {e['text'][:220]}")
    if result["missing"]:
        lines.append("Missing: " + "; ".join(result["missing"]))
    lines.append(result["recommendation"])
    return "\n".join(lines)[:2000]


async def analyze(case, settings, queue_runner=None):
    result=check_case(case)
    result["draft"]=rules_draft(case,result)
    result["claims"]=[]
    if not settings.ai_configured:
        result["notice"]="Evidence checks are running. Connect an AI model to enable AI drafting."
        return result
    # Evidence is untrusted data. No execution tools are available to the model.
    prompt={"case":{"reason":case["reason"],"amount":case["amount"],"currency":case["currency"]},"evidence":case["evidence"],"checks":result["findings"],"missing":result["missing"]}
    try:
        if settings.queue_drafter_configured:
            if queue_runner is None:
                raise ValueError("Drafting queue is unavailable")
            data = await queue_runner(case)
            model = data["model"]
        else:
            data = await provider_draft(prompt, settings)
            model = settings.ai_model
        result.update(draft=data["draft"], claims=data["claims"], engine="ai", model=model,
                      notice="AI draft: verify every statement against its source before approval. Citation checks validate IDs, not factual entailment.")
    except DraftFailure as exc:
        result["fallback_reason"] = exc.code
        result["notice"] = str(exc) + " Showing the evidence-check draft instead."
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        result["notice"]="AI drafting was unavailable or failed source validation. Showing the evidence-check draft instead."
    return result
