"""LangSmith RunTree → Jiminy DecisionTrace adapter.

Accepts a LangSmith ``Run`` / ``RunTree`` object or an equivalent plain dict
(no LangSmith SDK installation required when using dict form).

Usage::

    from adapters.langsmith import from_langsmith_run

    trace = from_langsmith_run(
        run_tree,
        agent_owner="Acme Insurance",
        submitted_by="State Regulatory Office",
        domain_profile="health_insurance_prior_auth",
    )
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from typing import Any

from schema.trace_schema import DecisionTrace, Step

# LangSmith run types that map to meaningful agent steps.
_STEP_RUN_TYPES: frozenset[str] = frozenset({"tool", "llm", "retriever"})

# Keys tried in order when extracting a human-readable task description.
_INPUT_KEYS = ("input", "question", "query", "prompt", "user_message", "text")

# Keys tried in order when extracting the final output.
_OUTPUT_KEYS = ("output", "result", "answer", "response", "content", "text")


def from_langsmith_run(
    run: Any,
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
) -> DecisionTrace:
    """Convert a LangSmith Run or RunTree (or equivalent dict) to a DecisionTrace.

    Args:
        run: A LangSmith RunTree/Run object or a dict with the same structure.
        agent_owner: Organisation that owns / operates the agent.
        submitted_by: Organisation submitting the trace for evaluation
            (must differ from *agent_owner*).
        domain_profile: Jiminy domain profile slug (default ``"general"``).
        task_description: Optional override; falls back to the run's input if omitted.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.

    Raises:
        TypeError: If *run* is not a dict or a duck-typed Run object.
        ValueError: If no evaluatable child runs are found, or Jiminy schema
            validation fails (e.g. self-evaluation guard).
    """
    raw = _as_dict(run)
    steps = _extract_steps(raw)
    if not steps:
        raise ValueError(
            "LangSmith run contains no evaluatable child runs. "
            "At least one tool, llm, or retriever step is required."
        )

    return DecisionTrace(
        trace_id=_coerce_str(raw.get("id"), fallback="unknown-trace", max_len=128),
        agent_id=_coerce_str(raw.get("name"), fallback="unknown-agent", max_len=128),
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        task_description=task_description or _extract_task(raw),
        timestamp=_parse_timestamp(raw.get("start_time")),
        domain_profile=domain_profile,
        steps=steps,
        final_output=_extract_output(raw),
        error_events=_extract_errors(raw),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _as_dict(run: Any) -> dict:
    """Coerce a LangSmith Run/RunTree object or dict to a plain dict."""
    if isinstance(run, dict):
        return run
    if hasattr(run, "model_dump"):
        return run.model_dump()
    if hasattr(run, "dict"):
        return run.dict()
    raise TypeError(
        f"Expected a LangSmith Run dict or RunTree object; got {type(run).__name__}"
    )


def _extract_steps(raw: dict) -> list[Step]:
    child_runs = raw.get("child_runs") or []
    steps: list[Step] = []
    for child in child_runs:
        child_dict = _as_dict(child)
        if child_dict.get("run_type", "") not in _STEP_RUN_TYPES:
            continue
        steps.append(_child_to_step(child_dict, step_id=len(steps) + 1))
    return steps


def _child_to_step(child: dict, step_id: int) -> Step:
    tool_name = _coerce_str(child.get("name"), fallback="unknown-tool", max_len=128)
    inputs = child.get("inputs") or {}
    outputs = child.get("outputs") or {}
    error = child.get("error")
    if error and not outputs:
        outputs = {"error": str(error)[:1000]}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return Step(
            step_id=step_id,
            tool=tool_name,
            input=inputs,
            output=outputs,
            reasoning=None,
        )


def _extract_errors(raw: dict) -> list[str]:
    events: list[str] = []
    for child in (raw.get("child_runs") or []):
        child_dict = _as_dict(child)
        err = child_dict.get("error")
        if err:
            label = child_dict.get("name", "unknown")
            events.append(f"{label}: {err}"[:1000])
    return events[:100]


def _parse_timestamp(ts: Any) -> datetime:
    if ts is None:
        return datetime.now(tz=UTC)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _extract_task(raw: dict) -> str:
    inputs = raw.get("inputs") or {}
    if isinstance(inputs, dict):
        for key in _INPUT_KEYS:
            val = inputs.get(key)
            if isinstance(val, str):
                val = val.strip()
                if val:
                    return val[:2000]
        stringified = str(inputs).strip()
        if stringified and stringified != "{}":
            return stringified[:2000]
    if isinstance(inputs, str):
        stripped = inputs.strip()
        if stripped:
            return stripped[:2000]
    name = _coerce_str(raw.get("name"), fallback="", max_len=128)
    return f"Agent run: {name}" if name else "Agent run"


def _extract_output(raw: dict) -> str:
    outputs = raw.get("outputs") or {}
    if isinstance(outputs, dict):
        for key in _OUTPUT_KEYS:
            val = outputs.get(key)
            if isinstance(val, str):
                val = val.strip()
                if val:
                    return val[:10000]
        stringified = str(outputs).strip()
        if stringified and stringified != "{}":
            return stringified[:10000]
    if isinstance(outputs, str):
        stripped = outputs.strip()
        if stripped:
            return stripped[:10000]
    err = raw.get("error")
    if err:
        return f"Error: {err}"[:10000]
    return "No output"


def _coerce_str(value: Any, *, fallback: str, max_len: int) -> str:
    if not isinstance(value, str):
        return fallback
    stripped = str(value).strip()
    return (stripped or fallback)[:max_len]
