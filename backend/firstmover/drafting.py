"""Bounded errors shared by the campaign planner, API and worker."""
from typing import Literal, get_args

DraftErrorCode = Literal[
    "invalid_input", "gateway_failed", "gateway_auth_failed", "gateway_rate_limited",
    "gateway_request_rejected", "gateway_unavailable", "invalid_model_schema",
    "model_output_truncated",
]


class DraftFailure(ValueError):
    def __init__(self, code, http_status=None):
        self.code = code if code in get_args(DraftErrorCode) else "gateway_failed"
        self.http_status = http_status
        super().__init__(self.code)
