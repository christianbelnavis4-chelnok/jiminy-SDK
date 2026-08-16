"""CrewAI CrewOutput → Jiminy DecisionTrace adapter.

Accepts the ``CrewOutput`` returned by ``crew.kickoff()`` — either the SDK
object or an equivalent plain dict (no CrewAI installation required).

Each ``TaskOutput`` in ``tasks_output`` becomes one ``Step``.  When consecutive
tasks are executed by different agents, the transition is recorded as an
escalation event — capturing the handoff chain that is the primary
accountability risk in multi-agent pipelines.

Usage::

    from adapters.crewai import from_crew_output

    result = crew.kickoff()

    trace = from_crew_output(
        result,
        agent_owner="Acme Insurance",
        submitted_by="State Regulatory Office",
        domain_profile="health_insurance_prior_auth",
    )

Dict form (no SDK required)::

    crew_output = {
        "raw": "Final combined output of the crew",
        "tasks_output": [
            {
                "description": "Research eligibility criteria",
                "name": "eligibility_task",
                "agent": "EligibilityAgent",
                "raw": "Member M001 is active and eligible.",
                "summary": "Eligible",
            },
            {
                "description": "Assess clinical criteria",
                "name": "criteria_task",
                "agent": "ClinicalAgent",
                "raw": "CPT 72148 meets criteria under policy section 4.2.",
                "summary": "Criteria met",
            },
        ],
        "token_usage": {"total_tokens": 820},
    }
"""

from __future__ import annotations

import uuid
import warnings
from datetime import UTC, datetime
from typing import Any

from schema.trace_schema import DecisionTrace, Step


def from_crew_output(
    crew_output: Any,
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
    trace_id: str | None = None,
) -> DecisionTrace:
    """Convert a CrewAI ``CrewOutput`` (or dict) to a ``DecisionTrace``.

    Args:
        crew_output: A ``CrewOutput`` SDK object or equivalent dict.
            Must contain a non-empty ``tasks_output`` list.
        agent_owner: Organisation that owns / operates the agent crew.
        submitted_by: Organisation submitting the trace for evaluation
            (must differ from *agent_owner*).
        domain_profile: Jiminy domain profile slug (default ``"general"``).
        task_description: Optional override; falls back to the first task's
            description or the crew's combined task summary.
        trace_id: Optional stable ID for this crew run.  A UUID is generated
            when omitted — supply one if your orchestration layer issues its
            own run IDs.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.

    Raises:
        ValueError: If ``tasks_output`` is empty (nothing to evaluate).
    """
    raw = _as_dict(crew_output)
    tasks = _get_tasks(raw)

    if not tasks:
        raise ValueError(
            "CrewOutput.tasks_output is empty. "
            "At least one completed task is required for evaluation."
        )

    steps = [_task_to_step(task, i + 1) for i, task in enumerate(tasks)]
    escalation_events = _detect_handoffs(tasks)

    first_agent = _coerce_str(_task_field(tasks[0], "agent"), max_len=128) or "crew"

    return DecisionTrace(
        trace_id=_coerce_str(trace_id, max_len=128) or str(uuid.uuid4()),
        agent_id=first_agent,
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        task_description=task_description or _derive_task_description(tasks),
        timestamp=datetime.now(tz=UTC),
        domain_profile=domain_profile,
        steps=steps,
        final_output=_extract_final_output(raw),
        escalation_events=escalation_events,
    )


# ---------------------------------------------------------------------------
# Dict coercion
# ---------------------------------------------------------------------------


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    # Duck-type attribute access as last resort
    return {
        "raw": getattr(obj, "raw", None),
        "tasks_output": getattr(obj, "tasks_output", None) or [],
        "token_usage": getattr(obj, "token_usage", None),
        "pydantic": getattr(obj, "pydantic", None),
        "json_dict": getattr(obj, "json_dict", None),
    }


def _task_as_dict(task: Any) -> dict:
    if isinstance(task, dict):
        return task
    if hasattr(task, "model_dump"):
        return task.model_dump()
    if hasattr(task, "dict"):
        return task.dict()
    return {
        "description": getattr(task, "description", None),
        "name": getattr(task, "name", None),
        "expected_output": getattr(task, "expected_output", None),
        "summary": getattr(task, "summary", None),
        "raw": getattr(task, "raw", None),
        "agent": getattr(task, "agent", None),
        "json_dict": getattr(task, "json_dict", None),
        "pydantic": str(getattr(task, "pydantic", None)) if getattr(task, "pydantic", None) else None,
    }


# ---------------------------------------------------------------------------
# Task extraction
# ---------------------------------------------------------------------------


def _get_tasks(raw: dict) -> list[dict]:
    tasks_raw = raw.get("tasks_output") or []
    return [_task_as_dict(t) for t in tasks_raw]


def _task_field(task: dict, field: str) -> Any:
    return task.get(field)


# ---------------------------------------------------------------------------
# Step construction
# ---------------------------------------------------------------------------


def _task_to_step(task: dict, step_id: int) -> Step:
    agent_role = _coerce_str(_task_field(task, "agent"), max_len=128) or "unknown-agent"
    description = _coerce_str(_task_field(task, "description"), max_len=2000)
    name = _coerce_str(_task_field(task, "name"), max_len=256)

    # tool = agent role (what role performed this step)
    # input  = task description / what the agent was asked to do
    # output = what the agent produced

    step_input: Any = description or name or "No description"
    step_output: Any = _extract_task_output(task)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return Step(
            step_id=step_id,
            tool=agent_role,
            input=step_input,
            output=step_output,
            reasoning=None,
        )


def _extract_task_output(task: dict) -> Any:
    # Prefer structured output when available, fall back to raw text.
    json_dict = task.get("json_dict")
    if isinstance(json_dict, dict) and json_dict:
        return json_dict

    raw = _coerce_str(task.get("raw"), max_len=10_000)
    if raw:
        return raw

    summary = _coerce_str(task.get("summary"), max_len=1_000)
    return summary or "No output"


# ---------------------------------------------------------------------------
# Escalation events — agent handoffs
# ---------------------------------------------------------------------------


def _detect_handoffs(tasks: list[dict]) -> list[str]:
    """Emit an escalation event for each agent-to-agent transition."""
    events: list[str] = []
    for i in range(len(tasks) - 1):
        from_agent = _coerce_str(_task_field(tasks[i], "agent"), max_len=128) or "unknown"
        to_agent = _coerce_str(_task_field(tasks[i + 1], "agent"), max_len=128) or "unknown"
        if from_agent != to_agent:
            events.append(f"Handoff: {from_agent} → {to_agent}")
    return events[:100]


# ---------------------------------------------------------------------------
# Task description and final output
# ---------------------------------------------------------------------------


def _derive_task_description(tasks: list[dict]) -> str:
    """Combine task descriptions into a crew-level task summary."""
    descriptions = []
    for task in tasks:
        desc = _coerce_str(_task_field(task, "description"), max_len=400)
        if desc:
            descriptions.append(desc)
    if not descriptions:
        return "Multi-agent crew run"
    if len(descriptions) == 1:
        return descriptions[0][:2000]
    combined = "; ".join(descriptions)
    return combined[:2000]


def _extract_final_output(raw: dict) -> str:
    # Use crew-level raw output (the combined final result).
    crew_raw = _coerce_str(raw.get("raw"), max_len=10_000)
    if crew_raw:
        return crew_raw

    # Fall back: last task's output.
    tasks = _get_tasks(raw)
    if tasks:
        return _extract_task_output(tasks[-1]) or "No output"

    return "No output"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _coerce_str(value: Any, *, max_len: int) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s[:max_len]
