"""Tests for adapters/otel/adapter.py."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock

import pytest

from adapters.otel.adapter import (
    _flatten_otlp_attributes,
    _ns_to_datetime,
    _split_root_children,
    _trace_id_str,
    from_otel_spans,
    from_otlp_json,
)
from schema.trace_schema import DecisionTrace

_OWNER = "Acme Insurance"
_SUBMITTER = "State Regulatory Office"

# ---------------------------------------------------------------------------
# Minimal span fixtures
# ---------------------------------------------------------------------------

_ROOT_NS = 1_688_000_000_000_000_000
_CHILD1_NS = 1_688_000_001_000_000_000
_CHILD2_NS = 1_688_000_002_000_000_000


def _root_span(**overrides) -> dict:
    s = {
        "trace_id": "abc123",
        "span_id": "span-root",
        "parent_span_id": None,
        "name": "AgentExecutor",
        "start_time_unix_nano": _ROOT_NS,
        "end_time_unix_nano": _ROOT_NS + 5_000_000_000,
        "attributes": {
            "input": "Approve prior auth for CPT 72148",
            "output": "Approved",
        },
        "status": {"code": 1, "message": ""},
    }
    s.update(overrides)
    return s


def _child_span(name: str, span_id: str, start: int, **overrides) -> dict:
    s = {
        "trace_id": "abc123",
        "span_id": span_id,
        "parent_span_id": "span-root",
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": start + 500_000_000,
        "attributes": {
            "input": {"key": "value"},
            "output": {"result": "ok"},
        },
        "status": {"code": 1, "message": ""},
    }
    s.update(overrides)
    return s


def _two_child_spans() -> list[dict]:
    return [
        _root_span(),
        _child_span("eligibility_check", "span-c1", _CHILD1_NS),
        _child_span("criteria_lookup", "span-c2", _CHILD2_NS),
    ]


# ---------------------------------------------------------------------------
# from_otel_spans — happy path
# ---------------------------------------------------------------------------


class TestFromOtelSpansHappyPath:
    def test_returns_decision_trace(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)

    def test_trace_id_from_root(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.trace_id == "abc123"

    def test_agent_id_from_root_name(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_id == "AgentExecutor"

    def test_agent_owner_and_submitter_set(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_owner == _OWNER
        assert result.submitted_by == _SUBMITTER

    def test_two_steps_extracted(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 2

    def test_steps_ordered_by_start_time(self):
        spans = [
            _root_span(),
            _child_span("second_tool", "span-c2", _CHILD2_NS),
            _child_span("first_tool", "span-c1", _CHILD1_NS),
        ]
        result = from_otel_spans(spans, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "first_tool"
        assert result.steps[1].tool == "second_tool"

    def test_step_ids_sequential(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert [s.step_id for s in result.steps] == [1, 2]

    def test_step_tool_names(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "eligibility_check"
        assert result.steps[1].tool == "criteria_lookup"

    def test_task_description_from_input_attr(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "prior auth" in result.task_description.lower()

    def test_final_output_from_output_attr(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.final_output == "Approved"

    def test_timestamp_from_root_start_time(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.timestamp.year == 2023  # _ROOT_NS is in 2023

    def test_domain_profile_default_general(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.domain_profile == "general"

    def test_domain_profile_override(self):
        result = from_otel_spans(
            _two_child_spans(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            domain_profile="health_insurance_prior_auth",
        )
        assert result.domain_profile == "health_insurance_prior_auth"

    def test_task_description_override(self):
        result = from_otel_spans(
            _two_child_spans(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            task_description="Custom description",
        )
        assert result.task_description == "Custom description"

    def test_no_error_events_when_clean(self):
        result = from_otel_spans(_two_child_spans(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.error_events == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestOtelErrors:
    def test_empty_spans_raises(self):
        with pytest.raises(ValueError, match="empty"):
            from_otel_spans([], agent_owner=_OWNER, submitted_by=_SUBMITTER)

    def test_root_only_raises(self):
        with pytest.raises(ValueError, match="no child spans"):
            from_otel_spans([_root_span()], agent_owner=_OWNER, submitted_by=_SUBMITTER)

    def test_error_span_captured(self):
        spans = [
            _root_span(),
            _child_span(
                "failing_tool",
                "span-err",
                _CHILD1_NS,
                **{"status": {"code": 2, "message": "Connection refused"}},
            ),
        ]
        result = from_otel_spans(spans, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.error_events) == 1
        assert "failing_tool" in result.error_events[0]
        assert "Connection refused" in result.error_events[0]

    def test_error_step_output_contains_error(self):
        spans = [
            _root_span(),
            _child_span(
                "flaky_check",
                "span-err",
                _CHILD1_NS,
                **{
                    "attributes": {"input": "query"},
                    "status": {"code": 2, "message": "Timeout"},
                },
            ),
        ]
        result = from_otel_spans(spans, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        step = result.steps[0]
        assert "Timeout" in str(step.output)

    def test_self_evaluation_guard_fires(self):
        with pytest.raises(Exception, match="self-evaluation"):
            from_otel_spans(
                _two_child_spans(),
                agent_owner="Acme Corp",
                submitted_by="Acme Corp",
            )


# ---------------------------------------------------------------------------
# OTLP JSON
# ---------------------------------------------------------------------------


_OTLP_JSON = {
    "resourceSpans": [
        {
            "resource": {"attributes": []},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "trace-xyz",
                            "spanId": "span-root",
                            "parentSpanId": "",
                            "name": "AgentRun",
                            "startTimeUnixNano": str(_ROOT_NS),
                            "endTimeUnixNano": str(_ROOT_NS + 5_000_000_000),
                            "attributes": [
                                {
                                    "key": "input",
                                    "value": {"stringValue": "process claim"},
                                }
                            ],
                            "status": {"code": "STATUS_CODE_OK", "message": ""},
                        },
                        {
                            "traceId": "trace-xyz",
                            "spanId": "span-child",
                            "parentSpanId": "span-root",
                            "name": "claim_check",
                            "startTimeUnixNano": str(_CHILD1_NS),
                            "endTimeUnixNano": str(_CHILD1_NS + 500_000_000),
                            "attributes": [
                                {
                                    "key": "output",
                                    "value": {"stringValue": "Valid"},
                                }
                            ],
                            "status": {"code": "STATUS_CODE_OK", "message": ""},
                        },
                    ]
                }
            ],
        }
    ]
}


class TestFromOtlpJson:
    def test_returns_decision_trace(self):
        result = from_otlp_json(_OTLP_JSON, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)

    def test_trace_id_from_otlp(self):
        result = from_otlp_json(_OTLP_JSON, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.trace_id == "trace-xyz"

    def test_one_step_extracted(self):
        result = from_otlp_json(_OTLP_JSON, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 1
        assert result.steps[0].tool == "claim_check"

    def test_status_code_string_parsed(self):
        # Verify STATUS_CODE_ERROR string maps to error correctly
        otlp = {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "root",
                                    "parentSpanId": "",
                                    "name": "Agent",
                                    "startTimeUnixNano": str(_ROOT_NS),
                                    "endTimeUnixNano": str(_ROOT_NS + 1_000_000_000),
                                    "attributes": [],
                                    "status": {"code": "STATUS_CODE_OK"},
                                },
                                {
                                    "traceId": "t1",
                                    "spanId": "child",
                                    "parentSpanId": "root",
                                    "name": "bad_tool",
                                    "startTimeUnixNano": str(_CHILD1_NS),
                                    "endTimeUnixNano": str(_CHILD1_NS + 500_000_000),
                                    "attributes": [],
                                    "status": {
                                        "code": "STATUS_CODE_ERROR",
                                        "message": "upstream failure",
                                    },
                                },
                            ]
                        }
                    ]
                }
            ]
        }
        result = from_otlp_json(otlp, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.error_events) == 1
        assert "bad_tool" in result.error_events[0]


# ---------------------------------------------------------------------------
# OTel SDK mock objects
# ---------------------------------------------------------------------------


class TestSdkSpanNormalisation:
    def _make_sdk_span(
        self,
        name: str,
        trace_id_int: int,
        span_id_int: int,
        parent_span_id_int: int | None,
        start_ns: int,
        end_ns: int,
        attrs: dict,
        status_code: int = 1,
    ) -> MagicMock:
        span = MagicMock()
        span.name = name
        span.start_time = start_ns
        span.end_time = end_ns
        span.attributes = attrs

        ctx = MagicMock()
        ctx.trace_id = trace_id_int
        ctx.span_id = span_id_int
        span.context = ctx

        if parent_span_id_int is not None:
            parent = MagicMock()
            parent.span_id = parent_span_id_int
            span.parent = parent
        else:
            span.parent = None

        status = MagicMock()
        status.status_code = MagicMock()
        status.status_code.value = status_code
        status.description = ""
        span.status = status

        return span

    def test_sdk_root_normalised(self):
        sdk_root = self._make_sdk_span(
            name="AgentRun",
            trace_id_int=0xABC,
            span_id_int=0x001,
            parent_span_id_int=None,
            start_ns=_ROOT_NS,
            end_ns=_ROOT_NS + 5_000_000_000,
            attrs={"input": "task input", "output": "task output"},
        )
        sdk_child = self._make_sdk_span(
            name="my_tool",
            trace_id_int=0xABC,
            span_id_int=0x002,
            parent_span_id_int=0x001,
            start_ns=_CHILD1_NS,
            end_ns=_CHILD1_NS + 500_000_000,
            attrs={"input": "child input"},
        )
        result = from_otel_spans(
            [sdk_root, sdk_child],
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
        )
        assert isinstance(result, DecisionTrace)
        assert result.agent_id == "AgentRun"
        assert len(result.steps) == 1
        assert result.steps[0].tool == "my_tool"

    def test_sdk_parent_linking(self):
        sdk_root = self._make_sdk_span(
            "Root", 0x1, 0x10, None, _ROOT_NS, _ROOT_NS + 1_000_000_000,
            {"output": "done"},
        )
        sdk_child = self._make_sdk_span(
            "ChildTool", 0x1, 0x20, 0x10, _CHILD1_NS, _CHILD1_NS + 500_000_000,
            {},
        )
        result = from_otel_spans(
            [sdk_root, sdk_child],
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            task_description="explicit task",
        )
        assert len(result.steps) == 1


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestFlattenOtlpAttributes:
    def test_string_value(self):
        attrs = [{"key": "foo", "value": {"stringValue": "bar"}}]
        assert _flatten_otlp_attributes(attrs) == {"foo": "bar"}

    def test_int_value(self):
        attrs = [{"key": "count", "value": {"intValue": 42}}]
        assert _flatten_otlp_attributes(attrs) == {"count": 42}

    def test_bool_value(self):
        attrs = [{"key": "flag", "value": {"boolValue": True}}]
        assert _flatten_otlp_attributes(attrs) == {"flag": True}

    def test_unknown_value_type_stringified(self):
        attrs = [{"key": "x", "value": {"arrayValue": [1, 2, 3]}}]
        result = _flatten_otlp_attributes(attrs)
        assert "x" in result

    def test_empty_list(self):
        assert _flatten_otlp_attributes([]) == {}


class TestSplitRootChildren:
    def test_root_identified_by_no_parent(self):
        spans = [
            {"trace_id": "t", "span_id": "root", "parent_span_id": None,
             "start_time_unix_nano": 100, "name": "Root",
             "end_time_unix_nano": 200, "attributes": {}, "status_code": 1, "status_message": ""},
            {"trace_id": "t", "span_id": "child", "parent_span_id": "root",
             "start_time_unix_nano": 110, "name": "Child",
             "end_time_unix_nano": 150, "attributes": {}, "status_code": 1, "status_message": ""},
        ]
        root, children = _split_root_children(spans)
        assert root["span_id"] == "root"
        assert len(children) == 1

    def test_multiple_roots_picks_earliest(self):
        spans = [
            {"trace_id": "t", "span_id": "r1", "parent_span_id": None,
             "start_time_unix_nano": 200, "name": "R1",
             "end_time_unix_nano": 300, "attributes": {}, "status_code": 1, "status_message": ""},
            {"trace_id": "t", "span_id": "r2", "parent_span_id": None,
             "start_time_unix_nano": 100, "name": "R2",
             "end_time_unix_nano": 300, "attributes": {}, "status_code": 1, "status_message": ""},
        ]
        root, _ = _split_root_children(spans)
        assert root["span_id"] == "r2"


class TestNsToDatetime:
    def test_zero_returns_utc_now(self):
        dt = _ns_to_datetime(0)
        assert dt.tzinfo is not None

    def test_valid_ns_converts(self):
        dt = _ns_to_datetime(_ROOT_NS)
        assert dt.year == 2023

    def test_result_is_utc_aware(self):
        dt = _ns_to_datetime(_ROOT_NS)
        assert dt.tzinfo == UTC


class TestTraceIdStr:
    def test_string_passthrough(self):
        assert _trace_id_str("abc123") == "abc123"

    def test_int_formatted_as_hex(self):
        result = _trace_id_str(0xABC)
        assert "abc" in result.lower()

    def test_empty_string_fallback(self):
        assert _trace_id_str("") == "unknown-trace"
