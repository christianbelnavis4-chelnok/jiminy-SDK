"""TraceBuilder — construct attested DecisionTraces for the Jiminy API.

Each step is signed with HMAC-SHA256, chained to the previous step's hash.
The trace carries a root hash over the identity fields and all step hashes.
The server re-derives these hashes using the same per-tenant HMAC key to
confirm the trace was not modified between emission and evaluation.

Usage::

    from datetime import datetime, timezone
    from jiminy_sdk import TraceBuilder

    builder = TraceBuilder(
        trace_id="...",
        agent_id="PA-Agent-01",
        agent_owner="Acme Insurance",
        submitted_by="Acme Compliance",
        task_description="Evaluate prior authorisation for MRI scan.",
        timestamp=datetime.now(tz=timezone.utc),
        domain_profile="health_insurance_prior_auth",
        hmac_key="your-per-tenant-hmac-key",
    )

    builder.add_step(1, "eligibility_check", input={"member_id": "123"}, output={"status": "Active"}, reasoning="Confirmed member active.")
    builder.add_step(2, "clinical_criteria_lookup", input={"cpt_code": "72148"}, output={"result": "Criteria met"}, reasoning="Applied InterQual IMG-1142.")
    builder.finalize("Approved. Auth reference: PA-2026-001.")

    trace = builder.build()  # dict — POST to POST /evaluate

The HMAC key must match the value configured as SECRET_HMAC_KEYS on the server
for your tenant. Traces built without a builder (or with the wrong key) will
evaluate with trace_integrity="broken" or "unverified".

Distribution: published to PyPI as `jiminy-sdk` (see
.github/workflows/sdk-publish-pypi.yml). Source install
(docs/QUICKSTART.md) also works if you want an unreleased change.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical(obj: object) -> str:
    """Deterministic compact JSON representation of obj."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _step_payload(
    step_id: int,
    tool: str,
    input_val: Any,
    output_val: Any,
    reasoning: str,
    prev_hash: str,
) -> bytes:
    payload = {
        "step_id": step_id,
        "tool": tool,
        "input": _canonical(input_val),
        "output": _canonical(output_val),
        "reasoning": reasoning,
        "prev_hash": prev_hash,
    }
    return _canonical(payload).encode()


def _root_payload(trace_id: str, agent_id: str, step_hashes: list[str]) -> bytes:
    payload = {
        "trace_id": trace_id,
        "agent_id": agent_id,
        "step_hashes": step_hashes,
    }
    return _canonical(payload).encode()


@dataclass
class _SignedStep:
    step_id: int
    tool: str
    input_val: Any
    output_val: Any
    reasoning: str | None
    step_hash: str


class TraceBuilder:
    """Builds a DecisionTrace dict with HMAC-SHA256 hash chaining.

    Call ``add_step()`` for each agent step in order, ``finalize()`` to set
    the final output, then ``build()`` to get the trace dict for the API.
    """

    def __init__(
        self,
        *,
        trace_id: str,
        agent_id: str,
        agent_owner: str,
        submitted_by: str,
        task_description: str,
        timestamp: datetime,
        domain_profile: str,
        hmac_key: str,
        escalation_events: list[str] | None = None,
        error_events: list[str] | None = None,
        callback_url: str | None = None,
        environment: str | None = None,
        framework: str | None = None,
    ) -> None:
        self._trace_id = trace_id
        self._agent_id = agent_id
        self._meta: dict[str, Any] = {
            "trace_id": trace_id,
            "agent_id": agent_id,
            "agent_owner": agent_owner,
            "submitted_by": submitted_by,
            "task_description": task_description,
            "timestamp": timestamp.isoformat(),
            "domain_profile": domain_profile,
            "final_output": "",
            "escalation_events": escalation_events or [],
            "error_events": error_events or [],
        }
        if callback_url is not None:
            self._meta["callback_url"] = callback_url
        # Self-serve SDK metadata — outside the
        # HMAC payload, see _root_payload/_step_payload above.
        if environment is not None:
            self._meta["environment"] = environment
        if framework is not None:
            self._meta["framework"] = framework
        self._key: bytes = hmac_key.encode() if isinstance(hmac_key, str) else hmac_key
        self._steps: list[_SignedStep] = []
        self._prev_hash: str = GENESIS_HASH

    def add_step(
        self,
        step_id: int,
        tool: str,
        *,
        input: Any,  # noqa: A002
        output: Any,
        reasoning: str | None = None,
    ) -> TraceBuilder:
        """Add a signed step to the trace. Must be called in step order."""
        h = hmac.new(
            self._key,
            _step_payload(step_id, tool, input, output, reasoning or "", self._prev_hash),
            hashlib.sha256,
        )
        step_hash = h.hexdigest()
        self._steps.append(_SignedStep(step_id, tool, input, output, reasoning, step_hash))
        self._prev_hash = step_hash
        return self

    def finalize(self, final_output: str) -> TraceBuilder:
        """Set the trace final output. Call before build()."""
        self._meta["final_output"] = final_output
        return self

    def build(self) -> dict[str, Any]:
        """Return the complete trace dict, ready for POST to /evaluate.

        Raises ValueError if no steps have been added.
        """
        if not self._steps:
            raise ValueError("TraceBuilder.build() called with no steps added.")

        step_hashes = [s.step_hash for s in self._steps]
        root_h = hmac.new(
            self._key,
            _root_payload(self._trace_id, self._agent_id, step_hashes),
            hashlib.sha256,
        )
        root_hash = root_h.hexdigest()

        steps = []
        for s in self._steps:
            step_dict: dict[str, Any] = {
                "step_id": s.step_id,
                "tool": s.tool,
                "input": s.input_val,
                "output": s.output_val,
                "step_hash": s.step_hash,
            }
            if s.reasoning is not None:
                step_dict["reasoning"] = s.reasoning
            steps.append(step_dict)

        return {
            **self._meta,
            "steps": steps,
            "trace_root_hash": root_hash,
        }
