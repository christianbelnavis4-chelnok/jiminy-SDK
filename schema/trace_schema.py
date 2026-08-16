"""
Jiminy — Agent Accountability Layer
DecisionTrace schema v1.0

Defines the canonical structure for an agent trace submitted for independent
accountability evaluation. The submitting party must be independent of the
agent owner — this is enforced as a hard schema constraint.
Soft issues (missing reasoning) emit UserWarnings so the validator CLI can
surface them without failing validation.
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

# Field length ceilings — sized to real-world maximums, not arbitrary caps.
_MAX_ID = 128            # trace_id, agent_id
_MAX_IDENTITY = 256      # agent_owner, submitted_by
_MAX_SLUG = 128          # domain_profile, tool name
_MAX_SHORT_TEXT = 2_000  # task_description
_MAX_LONG_TEXT = 10_000  # final_output, reasoning
_MAX_EVENT_ITEM = 1_000  # individual escalation / error event string
_MAX_EVENT_LIST = 100    # items per event list
_MAX_STEP_IO_STR = 65_536  # Step.input / Step.output when a plain string


def _require_str(value: Any, field: str, *, max_len: int) -> str:
    """Strip whitespace, reject empty, enforce max length."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty or whitespace-only")
    if len(value) > max_len:
        raise ValueError(
            f"{field} exceeds maximum length of {max_len} characters "
            f"(got {len(value)})"
        )
    return value


class Step(BaseModel):
    """A single tool-call step in an agent trace."""

    step_id: int
    tool: str
    input: Any
    output: Any
    reasoning: str | None = None
    step_hash: str | None = None

    @field_validator("tool", mode="before")
    @classmethod
    def _validate_tool(cls, v: Any) -> str:
        return _require_str(v, "tool", max_len=_MAX_SLUG)

    @field_validator("reasoning", mode="before")
    @classmethod
    def _validate_reasoning(cls, v: Any) -> str | None:
        if v is None:
            return None
        return _require_str(v, "reasoning", max_len=_MAX_LONG_TEXT)

    @field_validator("input", "output", mode="before")
    @classmethod
    def _validate_step_io(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) > _MAX_STEP_IO_STR:
            raise ValueError(
                f"Step input/output string exceeds maximum length of "
                f"{_MAX_STEP_IO_STR} characters"
            )
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.reasoning is None:
            warnings.warn(
                f"[JIMINY WARNING] Step {self.step_id} (tool: {self.tool!r}) "
                "has no reasoning field. Reasoning is required for trace auditability.",
                UserWarning,
                stacklevel=2,
            )


class DecisionTrace(BaseModel):
    """Top-level trace submitted to Jiminy for independent accountability evaluation."""

    trace_id: str
    agent_id: str
    agent_owner: str
    submitted_by: str
    task_description: str
    timestamp: datetime
    domain_profile: str
    steps: list[Step] = Field(min_length=1)
    final_output: str
    escalation_events: list[str] = Field(default_factory=list)
    error_events: list[str] = Field(default_factory=list)
    callback_url: HttpUrl | None = None
    trace_root_hash: str | None = None
    # Self-serve SDK metadata. Neither field is
    # part of the HMAC root or step payload (see docs/ATTESTATION_SPEC.md) —
    # additive fields only, no attestation impact on existing signed traces.
    # `source` (self_serve/design_partner/internal) is deliberately NOT a
    # trace field: it's derived server-side from the resolved API key's tier,
    # never trusted from client input.
    environment: str = "production"
    framework: str | None = None

    @field_validator("environment", mode="before")
    @classmethod
    def _validate_environment(cls, v: Any) -> str:
        v = _require_str(v, "environment", max_len=_MAX_SLUG)
        if v not in {"test", "production"}:
            raise ValueError("environment must be 'test' or 'production'")
        return v

    @field_validator("framework", mode="before")
    @classmethod
    def _validate_framework(cls, v: Any) -> str | None:
        if v is None:
            return None
        return _require_str(v, "framework", max_len=_MAX_SLUG)

    @field_validator("trace_id", "agent_id", mode="before")
    @classmethod
    def _validate_ids(cls, v: Any, info: Any) -> str:
        return _require_str(v, info.field_name, max_len=_MAX_ID)

    @field_validator("agent_owner", "submitted_by", mode="before")
    @classmethod
    def _validate_identity(cls, v: Any, info: Any) -> str:
        return _require_str(v, info.field_name, max_len=_MAX_IDENTITY)

    @field_validator("task_description", mode="before")
    @classmethod
    def _validate_task_description(cls, v: Any) -> str:
        return _require_str(v, "task_description", max_len=_MAX_SHORT_TEXT)

    @field_validator("final_output", mode="before")
    @classmethod
    def _validate_final_output(cls, v: Any) -> str:
        return _require_str(v, "final_output", max_len=_MAX_LONG_TEXT)

    @field_validator("domain_profile", mode="before")
    @classmethod
    def _validate_domain_profile(cls, v: Any) -> str:
        return _require_str(v, "domain_profile", max_len=_MAX_SLUG)

    @field_validator("escalation_events", "error_events", mode="before")
    @classmethod
    def _validate_event_lists(cls, v: Any, info: Any) -> list:
        if not isinstance(v, list):
            raise ValueError(f"{info.field_name} must be a list")
        if len(v) > _MAX_EVENT_LIST:
            raise ValueError(
                f"{info.field_name} exceeds maximum of {_MAX_EVENT_LIST} items "
                f"(got {len(v)})"
            )
        result = []
        for item in v:
            if not isinstance(item, str):
                raise ValueError(f"{info.field_name} items must be strings")
            item = item.strip()
            if len(item) > _MAX_EVENT_ITEM:
                raise ValueError(
                    f"{info.field_name} item exceeds maximum of {_MAX_EVENT_ITEM} characters"
                )
            result.append(item)
        return result

    @model_validator(mode="after")
    def check_independence(self) -> DecisionTrace:
        if self.submitted_by == self.agent_owner:
            raise ValueError(
                "Jiminy does not support self-evaluation. "
                "The submitting party must be independent of the agent owner."
            )
        return self
