from copy import deepcopy

SAMPLES = [
    {
        "key": "before", "title": "Renewal after confirmed cancellation", "customer": "Alex Morgan",
        "amount": "49.00", "currency": "USD", "reason": "CANCELED_RECURRING_BILLING",
        "paypal_id": None, "mode": "sample", "status": "Needs review", "deadline": None,
        "summary": "Customer says the renewal arrived after their subscription was cancelled.",
        "evidence": [
            {"id":"E1", "title":"Customer cancellation request", "kind":"request", "occurred_at":"2026-10-29T14:00:00Z", "provenance":"sample", "text":"Please cancel my subscription before the next renewal. Customer support ticket #1042."},
            {"id":"E2", "title":"Subscription cancellation record", "kind":"cancellation", "occurred_at":"2026-10-29T14:03:00Z", "provenance":"sample", "text":"Fixture: subscription I-DEMO-1042 changed to CANCELLED. Effective immediately."},
            {"id":"E3", "title":"Renewal payment record", "kind":"charge", "occurred_at":"2026-11-01T09:00:00Z", "provenance":"sample", "text":"Fixture: recurring charge of USD 49.00 recorded after cancellation."},
            {"id":"E4", "title":"Subscription agreement", "kind":"agreement", "occurred_at":"2026-10-01T09:00:00Z", "provenance":"sample", "text":"Monthly plan. Cancellation stops future renewals. Access remains through the paid period."},
        ],
    },
    {
        "key": "after", "title": "Cancellation received after renewal", "customer": "Taylor Reed",
        "amount": "29.00", "currency": "USD", "reason": "CANCELED_RECURRING_BILLING",
        "paypal_id":None, "mode":"sample", "status":"Needs review", "deadline":None,
        "summary":"Customer disputes a renewal. The recorded cancellation happened later that day.",
        "evidence":[
            {"id":"E1", "title":"Renewal payment record", "kind":"charge", "occurred_at":"2026-11-01T08:00:00Z", "provenance":"sample", "text":"Fixture: USD 29.00 renewal captured at 08:00 UTC."},
            {"id":"E2", "title":"Customer cancellation request", "kind":"request", "occurred_at":"2026-11-01T12:00:00Z", "provenance":"sample", "text":"Customer asked to cancel at noon and requested a refund of the morning renewal."},
            {"id":"E3", "title":"Subscription cancellation record", "kind":"cancellation", "occurred_at":"2026-11-01T12:05:00Z", "provenance":"sample", "text":"Fixture: subscription cancelled after the renewal."},
        ],
    },
    {
        "key":"missing", "title":"Cancellation claimed, confirmation missing", "customer":"Jordan Lee",
        "amount":"79.00", "currency":"USD", "reason":"CANCELED_RECURRING_BILLING",
        "paypal_id":None, "mode":"sample", "status":"Needs evidence", "deadline":None,
        "summary":"A customer reports cancelling last month. Only a support message and payment are available.",
        "evidence":[
            {"id":"E1", "title":"Customer support message", "kind":"request", "occurred_at":"2026-10-28T10:00:00Z", "provenance":"sample", "text":"I cancelled last month. Please stop charging me. This statement alone does not confirm a cancellation occurred."},
            {"id":"E2", "title":"Renewal payment record", "kind":"charge", "occurred_at":"2026-11-01T09:00:00Z", "provenance":"sample", "text":"Fixture: USD 79.00 renewal recorded."},
        ],
    },
]

def sample_cases():
    return deepcopy(SAMPLES)
