"""OpenAI Agents SDK → Jiminy DecisionTrace adapter.

Two integration surfaces are supported — no SDK installation required for
either when using the dict form:

1. **Span-based tracing** (``from_openai_agent_spans``):
   Use from a custom ``TracingProcessor`` that collects ``Trace`` and
   ``Span[T]`` objects during ``Runner.run()``.  Accepts SDK objects or
   equivalent plain dicts::

       {
           "trace_id": "trace-abc",
           "span_id": "span-001",
           "parent_id": None,          # None = root span
           "started_at": "2026-01-01T00:00:00Z",
           "ended_at":   "2026-01-01T00:00:05Z",
           "error": None,              # or {"message": "...", "data": "..."}
           "span_data": {
               "type": "function",     # "agent" | "function" | "llm" |
               "name": "search_kb",    #  "handoff" | "guardrail" | "custom"
               "input": '{"query": "return policy"}',   # JSON str
               "output": '{"result": "Returns accepted within 30 days"}',
           },
       }

2. **RunResult** (``from_run_result``):
   Pass the ``RunResult`` returned by ``Runner.run()`` directly.  Pairs
   ``ToolCallItem`` entries with their ``ToolCallOutputItem`` counterparts
   by tool-call ID; no tracing processor needed::

       result = await Runner.run(agent, "Approve claim XYZ")
       trace  = from_run_result(result, agent_owner=..., submitted_by=...)

Usage::

    from adapters.openai_agents import from_openai_agent_spans, from_run_result
"""

from __future__ import annotations

import json
import uuid
import warnings
from datetime import UTC, datetime
from typing import Any

from schema.trace_schema import DecisionTrace, Step

# Span data types that map to evaluatable Steps.
_STEP_SPAN_TYPES = frozenset({"function", "llm"})


# ---------------------------------------------------------------------------
# Public API — spans
# ---------------------------------------------------------------------------


def from_openai_agent_spans(
    trace: Any,
    spans: list[Any],
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
    final_output: str | None = None,
) -> DecisionTrace:
    """Convert an OpenAI Agents SDK Trace + Span list to a DecisionTrace.

    Args:
        trace: A ``Trace`` SDK object or a dict with ``trace_id`` and ``name``.
        spans: List of ``Span[T]`` SDK objects or equivalent dicts.
            See module docstring for the expected dict shape.
        agent_owner: Organisation that owns / operates the agent.
        submitted_by: Organisation submitting the trace for evaluation
            (must differ from *agent_owner*).
        domain_profile: Jiminy domain profile slug (default ``"general"``).
        task_description: Optional override; falls back to the root agent
            span's LLM input or the trace name.
        final_output: Optional override; falls back to the root agent span's
            ``output`` field.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.

    Raises:
        ValueError: If *spans* is empty or no evaluatable steps found.
    """
    if not spans:
        raise ValueError("spans must not be empty")

    trace_dict = _as_trace_dict(trace)
    norm_spans = [_normalise_span(s) for s in spans]

    root = _find_root(norm_spans)
    child_spans = [s for s in norm_spans if s is not root]
    child_spans.sort(key=lambda s: s["started_at_ns"])

    steps = [
        _span_to_step(s, i + 1)
        for i, s in enumerate(child_spans)
        if s["data_type"] in _STEP_SPAN_TYPES
    ]
    if not steps:
        raise ValueError(
            "OpenAI Agents trace contains no function or llm spans to map to Steps. "
            "At least one function or llm child span is required."
        )

    escalation_events = _collect_escalations(child_spans)
    error_events = _collect_span_errors(norm_spans)

    return DecisionTrace(
        trace_id=_coerce_str(trace_dict.get("trace_id"), max_len=128) or str(uuid.uuid4()),
        agent_id=_root_agent_name(root) or _coerce_str(trace_dict.get("name"), max_len=128) or "unknown-agent",
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        task_description=task_description or _extract_task_from_spans(root, child_spans, trace_dict),
        timestamp=_parse_iso(root.get("started_at")) or datetime.now(tz=UTC),
        domain_profile=domain_profile,
        steps=steps,
        final_output=final_output or _extract_output_from_spans(root) or "No output",
        escalation_events=escalation_events,
        error_events=error_events,
    )


# ---------------------------------------------------------------------------
# Public API — RunResult
# ---------------------------------------------------------------------------


def from_run_result(
    result: Any,
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
) -> DecisionTrace:
    """Convert an OpenAI Agents SDK ``RunResult`` to a DecisionTrace.

    Pairs ``ToolCallItem`` entries with their ``ToolCallOutputItem``
    counterparts by tool-call ID.  Works without a tracing processor — the
    ``RunResult`` returned by ``Runner.run()`` is sufficient.

    Args:
        result: A ``RunResult`` SDK object or an equivalent dict with
            ``input``, ``new_items``, ``last_agent``, and ``final_output``.
        agent_owner: Organisation that owns / operates the agent.
        submitted_by: Organisation submitting the trace for evaluation.
        domain_profile: Jiminy domain profile slug (default ``"general"``).
        task_description: Optional override; falls back to ``result.input``.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.

    Raises:
        ValueError: If no tool-call steps can be extracted from *result*.
    """
    raw = _as_run_result_dict(result)
    items = raw.get("new_items") or []

    tool_calls = {}      # call_id → {name, input_str}
    tool_outputs = {}    # call_id → output_str
    handoffs = []
    errors = []

    for item in items:
        item_type = _item_type(item)
        if item_type == "tool_call":
            call_id, name, input_str = _parse_tool_call_item(item)
            if call_id:
                tool_calls[call_id] = {"name": name, "input": input_str}
        elif item_type == "tool_call_output":
            call_id, output_str = _parse_tool_call_output_item(item)
            if call_id:
                tool_outputs[call_id] = output_str
        elif item_type == "handoff":
            src = _get(item, "source_agent") or _get(item, "agent")
            dst = _get(item, "target_agent")
            src_name = _get(src, "name") if src else "unknown"
            dst_name = _get(dst, "name") if dst else "unknown"
            handoffs.append(f"Handoff: {src_name} → {dst_name}")
        elif item_type == "message_output":
            err = _get(item, "error") or _get(_get(item, "raw_item"), "error")
            if err:
                errors.append(str(err)[:1000])

    steps = _build_steps_from_tool_calls(tool_calls, tool_outputs)
    if not steps:
        raise ValueError(
            "RunResult contains no tool-call steps. "
            "Ensure the agent made at least one tool call during the run."
        )

    input_ = raw.get("input") or ""
    task = task_description or _stringify(input_, max_len=2000) or "Agent run"

    final = _stringify(raw.get("final_output"), max_len=10_000) or "No output"

    last_agent = raw.get("last_agent")
    agent_id = _get(last_agent, "name") or "unknown-agent"

    return DecisionTrace(
        trace_id=_coerce_str(raw.get("trace_id") or raw.get("run_id"), max_len=128) or str(uuid.uuid4()),
        agent_id=str(agent_id)[:128],
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        task_description=task,
        timestamp=datetime.now(tz=UTC),
        domain_profile=domain_profile,
        steps=steps,
        final_output=final,
        escalation_events=handoffs[:100],
        error_events=errors[:100],
    )


# ---------------------------------------------------------------------------
# Span normalisation
# ---------------------------------------------------------------------------


def _as_trace_dict(trace: Any) -> dict:
    if isinstance(trace, dict):
        return trace
    if isinstance(trace, str):
        return {"trace_id": trace, "name": ""}
    return {
        "trace_id": _get(trace, "trace_id") or "",
        "name": _get(trace, "name") or "",
        "group_id": _get(trace, "group_id"),
        "metadata": _get(trace, "metadata"),
    }


def _normalise_span(span: Any) -> dict:
    if isinstance(span, dict):
        return _normalise_span_dict(span)
    return _normalise_span_sdk(span)


def _normalise_span_dict(span: dict) -> dict:
    span_data = span.get("span_data") or {}
    data_type = span_data.get("type", "unknown") if isinstance(span_data, dict) else "unknown"
    error_val = span.get("error")
    error_msg = None
    if isinstance(error_val, dict):
        error_msg = error_val.get("message")
    elif isinstance(error_val, str):
        error_msg = error_val

    return {
        "trace_id": span.get("trace_id", ""),
        "span_id": span.get("span_id", ""),
        "parent_id": span.get("parent_id"),
        "started_at": span.get("started_at"),
        "started_at_ns": _iso_to_ns(span.get("started_at")),
        "error": error_msg,
        "data_type": data_type,
        "data": span_data if isinstance(span_data, dict) else {},
    }


def _normalise_span_sdk(span: Any) -> dict:
    span_data = _get(span, "span_data")
    data_type = _detect_span_data_type(span_data)
    data = _extract_span_data_fields(span_data, data_type)

    error = _get(span, "error")
    error_msg = None
    if error is not None:
        error_msg = _get(error, "message") or str(error)

    started = _get(span, "started_at")
    return {
        "trace_id": _get(span, "trace_id") or "",
        "span_id": _get(span, "span_id") or "",
        "parent_id": _get(span, "parent_id"),
        "started_at": started,
        "started_at_ns": _iso_to_ns(started),
        "error": error_msg,
        "data_type": data_type,
        "data": data,
    }


def _detect_span_data_type(span_data: Any) -> str:
    if span_data is None:
        return "unknown"
    if isinstance(span_data, dict):
        return span_data.get("type", "unknown")
    cls = type(span_data).__name__.lower()
    for known in ("function", "llm", "handoff", "guardrail", "custom", "agent"):
        if known in cls:
            return known
    return "unknown"


def _extract_span_data_fields(span_data: Any, data_type: str) -> dict:
    if span_data is None:
        return {}
    if isinstance(span_data, dict):
        return span_data

    if data_type == "function":
        return {
            "type": "function",
            "name": _get(span_data, "name") or "",
            "input": _get(span_data, "input"),
            "output": _get(span_data, "output"),
        }
    if data_type == "llm":
        return {
            "type": "llm",
            "model": _get(span_data, "model") or "",
            "input": _get(span_data, "input"),
            "output": _get(span_data, "output"),
        }
    if data_type == "agent":
        return {
            "type": "agent",
            "name": _get(span_data, "name") or "",
            "handoffs": _get(span_data, "handoffs") or [],
            "output": _get(span_data, "output"),
            "tools": _get(span_data, "tools") or [],
        }
    if data_type == "handoff":
        return {
            "type": "handoff",
            "from_agent": _get(span_data, "from_agent") or "",
            "to_agent": _get(span_data, "to_agent") or "",
        }
    if data_type == "guardrail":
        return {
            "type": "guardrail",
            "name": _get(span_data, "name") or "",
            "triggered": bool(_get(span_data, "triggered")),
        }
    return {
        "type": data_type,
        "name": _get(span_data, "name") or "",
        "data": _get(span_data, "data") or {},
    }


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------


def _find_root(spans: list[dict]) -> dict:
    roots = [s for s in spans if not s["parent_id"]]
    if not roots:
        # Fallback: earliest span
        roots = [min(spans, key=lambda s: s["started_at_ns"])]
    # Prefer the AgentSpanData root if there are multiple rootless spans
    agent_roots = [s for s in roots if s["data_type"] == "agent"]
    if agent_roots:
        return min(agent_roots, key=lambda s: s["started_at_ns"])
    return min(roots, key=lambda s: s["started_at_ns"])


# ---------------------------------------------------------------------------
# Step construction from spans
# ---------------------------------------------------------------------------


def _span_to_step(span: dict, step_id: int) -> Step:
    data = span.get("data") or {}
    data_type = span.get("data_type", "unknown")

    if data_type == "function":
        tool_name = str(data.get("name") or "unknown-function")[:128]
        raw_input = data.get("input")
        raw_output = data.get("output")
        step_input = _parse_json_str(raw_input) if isinstance(raw_input, str) else (raw_input or {})
        step_output = _parse_json_str(raw_output) if isinstance(raw_output, str) else (raw_output or {})
    elif data_type == "llm":
        model = str(data.get("model") or "llm")[:128]
        tool_name = f"llm:{model}"
        step_input = data.get("input") or {}
        step_output = data.get("output") or {}
    else:
        tool_name = str(data.get("name") or data_type)[:128]
        step_input = data
        step_output = {}

    if span.get("error") and not step_output:
        step_output = {"error": span["error"][:1000]}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return Step(
            step_id=step_id,
            tool=tool_name or "unknown-tool",
            input=step_input,
            output=step_output,
            reasoning=None,
        )


# ---------------------------------------------------------------------------
# Escalation / error collection from spans
# ---------------------------------------------------------------------------


def _collect_escalations(spans: list[dict]) -> list[str]:
    events: list[str] = []
    for span in spans:
        data = span.get("data") or {}
        if span.get("data_type") == "handoff":
            src = data.get("from_agent") or "unknown"
            dst = data.get("to_agent") or "unknown"
            events.append(f"Handoff: {src} → {dst}")
        elif span.get("data_type") == "guardrail" and data.get("triggered"):
            name = data.get("name") or "guardrail"
            events.append(f"Guardrail triggered: {name}")
    return events[:100]


def _collect_span_errors(spans: list[dict]) -> list[str]:
    events: list[str] = []
    for span in spans:
        err = span.get("error")
        if err:
            data = span.get("data") or {}
            label = data.get("name") or span.get("data_type") or "span"
            events.append(f"{label}: {err}"[:1000])
    return events[:100]


# ---------------------------------------------------------------------------
# Task / output extraction from spans
# ---------------------------------------------------------------------------


def _root_agent_name(root: dict) -> str:
    data = root.get("data") or {}
    return str(data.get("name") or "")[:128]


def _extract_task_from_spans(root: dict, children: list[dict], trace: dict) -> str:
    # First LLM span input often contains the initial user message.
    for span in children:
        if span.get("data_type") == "llm":
            msgs = (span.get("data") or {}).get("input")
            if isinstance(msgs, list) and msgs:
                last = msgs[-1]
                content = last.get("content") if isinstance(last, dict) else None
                if isinstance(content, str) and content.strip():
                    return content.strip()[:2000]
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                return text[:2000]
            if isinstance(msgs, str) and msgs.strip():
                return msgs.strip()[:2000]

    # Fall back to trace name or root agent name.
    name = _root_agent_name(root) or trace.get("name") or ""
    return f"Agent run: {name}" if name else "Agent run"


def _extract_output_from_spans(root: dict) -> str:
    data = root.get("data") or {}
    output = data.get("output")
    if isinstance(output, str) and output.strip():
        return output.strip()[:10_000]
    if output is not None:
        s = _stringify(output, max_len=10_000)
        if s:
            return s
    return ""


# ---------------------------------------------------------------------------
# RunResult helpers
# ---------------------------------------------------------------------------


def _as_run_result_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    return {
        "input": _get(result, "input"),
        "new_items": _get(result, "new_items") or [],
        "last_agent": _get(result, "last_agent"),
        "final_output": _get(result, "final_output"),
        "trace_id": _get(result, "trace_id") or _get(result, "run_id"),
    }


def _item_type(item: Any) -> str:
    """Classify a RunResultItem by duck-typing."""
    if isinstance(item, dict):
        return item.get("type", "unknown")
    cls = type(item).__name__.lower()
    if "toolcalloutput" in cls:
        return "tool_call_output"
    if "toolcall" in cls:
        return "tool_call"
    if "handoff" in cls:
        return "handoff"
    if "message" in cls:
        return "message_output"
    return "unknown"


def _parse_tool_call_item(item: Any) -> tuple[str, str, Any]:
    """Return (call_id, function_name, input) from a ToolCallItem."""
    if isinstance(item, dict):
        raw = item.get("raw_item") or {}
        fn = raw.get("function") or {}
        call_id = raw.get("id") or item.get("id") or ""
        name = fn.get("name") or raw.get("name") or ""
        args = fn.get("arguments") or raw.get("arguments") or raw.get("input") or {}
    else:
        raw = _get(item, "raw_item") or item
        fn = _get(raw, "function") or raw
        call_id = _get(raw, "id") or _get(item, "id") or ""
        name = _get(fn, "name") or _get(raw, "name") or ""
        args = _get(fn, "arguments") or _get(raw, "arguments") or _get(raw, "input") or {}

    parsed_args = _parse_json_str(args) if isinstance(args, str) else args
    return str(call_id), str(name)[:128], parsed_args


def _parse_tool_call_output_item(item: Any) -> tuple[str, str]:
    """Return (call_id, output_str) from a ToolCallOutputItem."""
    if isinstance(item, dict):
        output = item.get("output") or ""
        raw = item.get("raw_item") or {}
        call_id = raw.get("id") or raw.get("tool_call_id") or item.get("id") or ""
    else:
        output = _get(item, "output") or ""
        raw = _get(item, "raw_item") or item
        call_id = _get(raw, "id") or _get(raw, "tool_call_id") or _get(item, "id") or ""

    return str(call_id), str(output)[:65_536]


def _build_steps_from_tool_calls(
    tool_calls: dict[str, dict],
    tool_outputs: dict[str, str],
) -> list[Step]:
    steps: list[Step] = []
    for i, (call_id, call) in enumerate(tool_calls.items(), start=1):
        output_str = tool_outputs.get(call_id, "")
        output = _parse_json_str(output_str) if output_str else {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            steps.append(Step(
                step_id=i,
                tool=call["name"] or "unknown-tool",
                input=call["input"] or {},
                output=output,
                reasoning=None,
            ))
    return steps


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _get(obj: Any, *attrs: str) -> Any:
    """Safe multi-attribute getter for SDK objects or dicts."""
    for attr in attrs:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(attr)
        else:
            obj = getattr(obj, attr, None)
    return obj


def _coerce_str(value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _stringify(value: Any, *, max_len: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:max_len]
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)[:max_len]
        except (TypeError, ValueError):
            pass
    return str(value)[:max_len]


def _parse_json_str(s: str) -> Any:
    """Try to parse a JSON string; return the raw string on failure."""
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


def _iso_to_ns(ts: Any) -> int:
    """Convert an ISO 8601 datetime string to nanoseconds since epoch."""
    if ts is None:
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1e9)
        except ValueError:
            pass
    return 0


def _parse_iso(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None
