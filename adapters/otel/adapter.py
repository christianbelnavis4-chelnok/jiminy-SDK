"""OpenTelemetry spans → Jiminy DecisionTrace adapter.

Supports two input forms — no OTel SDK installation required for either:

1. **Flat span list** (``from_otel_spans``):
   A ``list`` of ``ReadableSpan`` objects from the OTel Python SDK,
   *or* a ``list`` of plain dicts with keys::

       {
           "trace_id": "0af7651916cd43dd8448eb211c80319c",  # hex str or int
           "span_id":  "00f067aa0ba902b7",                  # hex str or int
           "parent_span_id": "b9c7c989f97918e1",            # None for root
           "name": "tool.eligibility_check",
           "start_time_unix_nano": 1688000000000000000,
           "end_time_unix_nano":   1688000001000000000,
           "attributes": {...},   # flat str→Any dict or OTel AnyValue list
           "status": {"code": 0, "message": ""},   # 2 = ERROR
       }

2. **OTLP JSON export** (``from_otlp_json``):
   The top-level ``resourceSpans`` structure emitted by OTel collectors
   or the OTel Python SDK's ``OTLPSpanExporter``.

Usage::

    from adapters.otel import from_otel_spans, from_otlp_json

    trace = from_otel_spans(
        spans,
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

# OTel status codes: 0 = UNSET, 1 = OK, 2 = ERROR
_STATUS_ERROR = 2

# OTel attribute keys for LLM Semantic Conventions (GenAI SIG).
_INPUT_ATTR_KEYS = (
    "gen_ai.input.messages",
    "input",
    "tool.input",
    "llm.input_messages",
    "gen_ai.prompt",
)
_OUTPUT_ATTR_KEYS = (
    "gen_ai.output.messages",
    "output",
    "tool.output",
    "llm.output_messages",
    "gen_ai.completion",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def from_otel_spans(
    spans: list[Any],
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
) -> DecisionTrace:
    """Convert a flat list of OTel spans (SDK objects or dicts) to a DecisionTrace.

    Args:
        spans: ``ReadableSpan`` objects or equivalent dicts (see module docstring).
        agent_owner: Organisation that owns / operates the agent.
        submitted_by: Organisation submitting the trace for evaluation
            (must differ from *agent_owner*).
        domain_profile: Jiminy domain profile slug (default ``"general"``).
        task_description: Optional override; falls back to root span attributes.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.

    Raises:
        ValueError: If *spans* is empty, no root span can be identified, or
            no child spans are present to map to Steps.
    """
    if not spans:
        raise ValueError("spans must not be empty")

    normalised = [_normalise_span(s) for s in spans]
    root, children = _split_root_children(normalised)

    if not children:
        raise ValueError(
            "OTel trace contains no child spans to map to Steps. "
            "At least one non-root span is required."
        )

    children.sort(key=lambda s: s["start_time_unix_nano"])
    steps = [_span_to_step(child, i + 1) for i, child in enumerate(children)]

    return DecisionTrace(
        trace_id=_trace_id_str(root["trace_id"]),
        agent_id=root["name"][:128] or "unknown-agent",
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        task_description=task_description or _extract_task(root),
        timestamp=_ns_to_datetime(root["start_time_unix_nano"]),
        domain_profile=domain_profile,
        steps=steps,
        final_output=_extract_output(root),
        error_events=_collect_errors(children),
    )


def from_otlp_json(
    otlp_json: dict,
    *,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    task_description: str | None = None,
) -> DecisionTrace:
    """Convert an OTLP JSON export (``resourceSpans`` structure) to a DecisionTrace.

    Args:
        otlp_json: The top-level dict from an OTLP JSON export, containing
            a ``"resourceSpans"`` key.

    Returns:
        A validated :class:`~schema.trace_schema.DecisionTrace`.
    """
    spans = _extract_spans_from_otlp(otlp_json)
    return from_otel_spans(
        spans,
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        domain_profile=domain_profile,
        task_description=task_description,
    )


# ---------------------------------------------------------------------------
# OTLP JSON extraction
# ---------------------------------------------------------------------------


def _extract_spans_from_otlp(otlp: dict) -> list[dict]:
    """Flatten ``resourceSpans`` → ``scopeSpans`` → ``spans`` into a flat list."""
    flat: list[dict] = []
    for resource_span in otlp.get("resourceSpans", []):
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                flat.append(_normalise_otlp_span(span))
    return flat


def _normalise_otlp_span(span: dict) -> dict:
    """Normalise an OTLP JSON span dict into the flat internal form."""
    raw_attrs = span.get("attributes", [])
    attrs = _flatten_otlp_attributes(raw_attrs)
    status_obj = span.get("status") or {}
    status_code = status_obj.get("code", 0)
    if isinstance(status_code, str):
        status_code = {"STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}.get(
            status_code, 0
        )
    return {
        "trace_id": span.get("traceId", ""),
        "span_id": span.get("spanId", ""),
        "parent_span_id": span.get("parentSpanId") or None,
        "name": span.get("name", ""),
        "start_time_unix_nano": int(span.get("startTimeUnixNano", 0)),
        "end_time_unix_nano": int(span.get("endTimeUnixNano", 0)),
        "attributes": attrs,
        "status_code": status_code,
        "status_message": status_obj.get("message", ""),
    }


def _flatten_otlp_attributes(attrs: list[dict]) -> dict:
    """Convert OTLP ``[{key, value: {stringValue/intValue/...}}]`` to a flat dict."""
    result: dict = {}
    for item in attrs:
        key = item.get("key", "")
        value_obj = item.get("value") or {}
        for vtype in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if vtype in value_obj:
                result[key] = value_obj[vtype]
                break
        else:
            # Structured value — stringify it
            result[key] = str(value_obj)
    return result


# ---------------------------------------------------------------------------
# Span normalisation (SDK objects + plain dicts)
# ---------------------------------------------------------------------------


def _normalise_span(span: Any) -> dict:
    """Coerce an OTel ``ReadableSpan`` or plain dict to the flat internal form."""
    if isinstance(span, dict):
        return _normalise_dict_span(span)
    # Duck-type the OTel SDK ReadableSpan
    return _normalise_sdk_span(span)


def _normalise_dict_span(span: dict) -> dict:
    # Already in internal normalised form — produced by _normalise_otlp_span.
    if "status_code" in span and "status" not in span:
        return {
            "trace_id": span.get("trace_id", ""),
            "span_id": span.get("span_id", ""),
            "parent_span_id": span.get("parent_span_id"),
            "name": span.get("name", ""),
            "start_time_unix_nano": int(span.get("start_time_unix_nano", 0)),
            "end_time_unix_nano": int(span.get("end_time_unix_nano", 0)),
            "attributes": span.get("attributes") or {},
            "status_code": int(span.get("status_code", 0)),
            "status_message": span.get("status_message", ""),
        }

    # External form with a "status" dict (plain user-supplied span dicts).
    status = span.get("status") or {}
    if isinstance(status, dict):
        status_code = status.get("code", 0)
        status_message = status.get("message", "")
    else:
        status_code = int(status)
        status_message = ""

    attrs = span.get("attributes") or {}
    if isinstance(attrs, list):
        attrs = _flatten_otlp_attributes(attrs)

    return {
        "trace_id": span.get("trace_id", ""),
        "span_id": span.get("span_id", ""),
        "parent_span_id": span.get("parent_span_id") or None,
        "name": span.get("name", ""),
        "start_time_unix_nano": int(span.get("start_time_unix_nano", 0)),
        "end_time_unix_nano": int(span.get("end_time_unix_nano", 0)),
        "attributes": attrs,
        "status_code": status_code,
        "status_message": status_message,
    }


def _normalise_sdk_span(span: Any) -> dict:
    """Normalise an OTel Python SDK ``ReadableSpan``."""
    ctx = getattr(span, "context", None)

    trace_id = ""
    span_id = ""
    if ctx:
        trace_id = format(getattr(ctx, "trace_id", 0), "032x")
        span_id = format(getattr(ctx, "span_id", 0), "016x")

    parent = getattr(span, "parent", None)
    parent_span_id: str | None = None
    if parent:
        parent_span_id = format(getattr(parent, "span_id", 0), "016x")

    status = getattr(span, "status", None)
    status_code = 0
    status_message = ""
    if status:
        raw_code = getattr(status, "status_code", None)
        if raw_code is not None:
            status_code = int(raw_code.value) if hasattr(raw_code, "value") else int(raw_code)
        status_message = getattr(status, "description", "") or ""

    attrs = dict(getattr(span, "attributes", {}) or {})

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": getattr(span, "name", "") or "",
        "start_time_unix_nano": getattr(span, "start_time", 0) or 0,
        "end_time_unix_nano": getattr(span, "end_time", 0) or 0,
        "attributes": attrs,
        "status_code": status_code,
        "status_message": status_message,
    }


# ---------------------------------------------------------------------------
# Root / children split
# ---------------------------------------------------------------------------


def _split_root_children(spans: list[dict]) -> tuple[dict, list[dict]]:
    """Identify the root span and return (root, children).

    Root = span with no parent_span_id. If multiple candidates exist
    (disconnected spans), picks the one with the earliest start time.
    """
    roots = [s for s in spans if not s["parent_span_id"]]
    if not roots:
        # Fallback: treat the earliest span as root
        roots = [min(spans, key=lambda s: s["start_time_unix_nano"])]

    root = min(roots, key=lambda s: s["start_time_unix_nano"])
    children = [s for s in spans if s is not root]
    return root, children


# ---------------------------------------------------------------------------
# Step construction
# ---------------------------------------------------------------------------


def _span_to_step(span: dict, step_id: int) -> Step:
    attrs = span.get("attributes") or {}
    tool_name = span["name"][:128] or "unknown-tool"
    inputs = _extract_attr(attrs, _INPUT_ATTR_KEYS) or attrs
    outputs = _extract_attr(attrs, _OUTPUT_ATTR_KEYS)

    is_error = span.get("status_code") == _STATUS_ERROR
    if is_error and not outputs:
        outputs = {"error": span.get("status_message", "span error")[:1000]}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return Step(
            step_id=step_id,
            tool=tool_name,
            input=inputs,
            output=outputs or {},
            reasoning=None,
        )


def _collect_errors(children: list[dict]) -> list[str]:
    events: list[str] = []
    for span in children:
        if span.get("status_code") == _STATUS_ERROR:
            msg = span.get("status_message") or "error"
            events.append(f"{span['name']}: {msg}"[:1000])
    return events[:100]


# ---------------------------------------------------------------------------
# Attribute / IO extraction
# ---------------------------------------------------------------------------


def _extract_attr(attrs: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in attrs:
            return attrs[key]
    return None


def _extract_task(root: dict) -> str:
    attrs = root.get("attributes") or {}
    val = _extract_attr(attrs, _INPUT_ATTR_KEYS)
    if isinstance(val, str):
        stripped = val.strip()
        if stripped:
            return stripped[:2000]
    if isinstance(val, (list, dict)):
        s = str(val)[:2000]
        if s:
            return s
    name = root.get("name", "").strip()
    return f"Agent run: {name}" if name else "Agent run"


def _extract_output(root: dict) -> str:
    attrs = root.get("attributes") or {}
    val = _extract_attr(attrs, _OUTPUT_ATTR_KEYS)
    if isinstance(val, str):
        stripped = val.strip()
        if stripped:
            return stripped[:10000]
    if isinstance(val, (list, dict)):
        s = str(val)[:10000]
        if s:
            return s
    is_error = root.get("status_code") == _STATUS_ERROR
    if is_error:
        msg = root.get("status_message") or "error"
        return f"Error: {msg}"[:10000]
    return "No output"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _trace_id_str(trace_id: Any) -> str:
    if isinstance(trace_id, int):
        return format(trace_id, "032x")[:128]
    return str(trace_id).strip()[:128] or "unknown-trace"


def _ns_to_datetime(ns: int) -> datetime:
    if not ns:
        return datetime.now(tz=UTC)
    try:
        return datetime.fromtimestamp(ns / 1e9, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return datetime.now(tz=UTC)
