"""Tests for adapters/openai_agents/adapter.py."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from adapters.openai_agents.adapter import (
    _detect_span_data_type,
    _find_root,
    _iso_to_ns,
    _item_type,
    _parse_json_str,
    from_openai_agent_spans,
    from_run_result,
)
from schema.trace_schema import DecisionTrace

_OWNER = "Acme Insurance"
_SUBMITTER = "State Regulatory Office"

_T0 = "2026-07-01T10:00:00Z"
_T1 = "2026-07-01T10:00:01Z"
_T2 = "2026-07-01T10:00:02Z"
_T3 = "2026-07-01T10:00:05Z"


# ---------------------------------------------------------------------------
# Span dict fixtures
# ---------------------------------------------------------------------------


def _agent_span(**overrides) -> dict:
    s = {
        "trace_id": "trace-001",
        "span_id": "span-root",
        "parent_id": None,
        "started_at": _T0,
        "ended_at": _T3,
        "error": None,
        "span_data": {
            "type": "agent",
            "name": "SupportAgent",
            "handoffs": [],
            "output": "Your claim is approved.",
            "tools": ["search_kb", "create_ticket"],
        },
    }
    s.update(overrides)
    return s


def _function_span(name: str, span_id: str, started_at: str, **overrides) -> dict:
    s = {
        "trace_id": "trace-001",
        "span_id": span_id,
        "parent_id": "span-root",
        "started_at": started_at,
        "ended_at": started_at,
        "error": None,
        "span_data": {
            "type": "function",
            "name": name,
            "input": '{"query": "eligibility"}',
            "output": '{"status": "eligible"}',
        },
    }
    s.update(overrides)
    return s


def _llm_span(span_id: str, started_at: str, **overrides) -> dict:
    s = {
        "trace_id": "trace-001",
        "span_id": span_id,
        "parent_id": "span-root",
        "started_at": started_at,
        "ended_at": started_at,
        "error": None,
        "span_data": {
            "type": "llm",
            "model": "gpt-4o",
            "input": [{"role": "user", "content": "Approve claim ABC"}],
            "output": [{"role": "assistant", "content": "Claim approved."}],
        },
    }
    s.update(overrides)
    return s


def _two_span_list() -> list[dict]:
    return [
        _agent_span(),
        _function_span("search_kb", "span-f1", _T1),
        _function_span("create_ticket", "span-f2", _T2),
    ]


# ---------------------------------------------------------------------------
# from_openai_agent_spans — happy path (dict form)
# ---------------------------------------------------------------------------


class TestFromSpansHappyPath:
    _trace = {"trace_id": "trace-001", "name": "SupportWorkflow"}

    def test_returns_decision_trace(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert isinstance(result, DecisionTrace)

    def test_trace_id_from_trace(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.trace_id == "trace-001"

    def test_agent_id_from_root_agent_span(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.agent_id == "SupportAgent"

    def test_two_function_steps(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert len(result.steps) == 2

    def test_step_ids_sequential(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert [s.step_id for s in result.steps] == [1, 2]

    def test_step_tool_names(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].tool == "search_kb"
        assert result.steps[1].tool == "create_ticket"

    def test_function_span_input_parsed_from_json(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].input == {"query": "eligibility"}

    def test_function_span_output_parsed_from_json(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].output == {"status": "eligible"}

    def test_final_output_from_root_agent_span(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert "approved" in result.final_output.lower()

    def test_final_output_override(self):
        result = from_openai_agent_spans(
            self._trace,
            _two_span_list(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            final_output="Explicit override",
        )
        assert result.final_output == "Explicit override"

    def test_task_description_override(self):
        result = from_openai_agent_spans(
            self._trace,
            _two_span_list(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            task_description="Custom task",
        )
        assert result.task_description == "Custom task"

    def test_domain_profile_default(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.domain_profile == "general"

    def test_domain_profile_override(self):
        result = from_openai_agent_spans(
            self._trace,
            _two_span_list(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            domain_profile="hr_recruitment",
        )
        assert result.domain_profile == "hr_recruitment"

    def test_no_escalation_events_when_clean(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.escalation_events == []

    def test_no_error_events_when_clean(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.error_events == []

    def test_timestamp_from_root_started_at(self):
        result = from_openai_agent_spans(
            self._trace, _two_span_list(), agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.timestamp.year == 2026

    def test_steps_ordered_by_started_at(self):
        spans = [
            _agent_span(),
            _function_span("b_tool", "span-b", _T2),
            _function_span("a_tool", "span-a", _T1),
        ]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].tool == "a_tool"
        assert result.steps[1].tool == "b_tool"


# ---------------------------------------------------------------------------
# LLM spans
# ---------------------------------------------------------------------------


class TestLLMSpans:
    _trace = {"trace_id": "trace-llm", "name": "LLMWorkflow"}

    def test_llm_span_included_as_step(self):
        spans = [_agent_span(), _llm_span("span-llm", _T1)]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert len(result.steps) == 1
        assert result.steps[0].tool == "llm:gpt-4o"

    def test_llm_span_input_preserved(self):
        spans = [_agent_span(), _llm_span("span-llm", _T1)]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].input == [{"role": "user", "content": "Approve claim ABC"}]

    def test_task_description_extracted_from_llm_input(self):
        spans = [
            _agent_span(**{
                "span_data": {"type": "agent", "name": "Agent", "output": "done", "handoffs": [], "tools": []},
            }),
            _llm_span("span-llm", _T1),
        ]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert "Approve claim ABC" in result.task_description


# ---------------------------------------------------------------------------
# Handoffs and guardrails → escalation events
# ---------------------------------------------------------------------------


class TestEscalationEvents:
    _trace = {"trace_id": "trace-x", "name": "MultiAgent"}

    def _handoff_span(self) -> dict:
        return {
            "trace_id": "trace-x",
            "span_id": "span-handoff",
            "parent_id": "span-root",
            "started_at": _T2,
            "ended_at": _T2,
            "error": None,
            "span_data": {
                "type": "handoff",
                "from_agent": "TriageAgent",
                "to_agent": "SpecialistAgent",
            },
        }

    def _guardrail_span(self, triggered: bool = True) -> dict:
        return {
            "trace_id": "trace-x",
            "span_id": "span-guardrail",
            "parent_id": "span-root",
            "started_at": _T1,
            "ended_at": _T1,
            "error": None,
            "span_data": {
                "type": "guardrail",
                "name": "PII filter",
                "triggered": triggered,
            },
        }

    def test_handoff_span_becomes_escalation_event(self):
        spans = [_agent_span(), _function_span("f", "sf", _T1), self._handoff_span()]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert len(result.escalation_events) == 1
        assert "TriageAgent" in result.escalation_events[0]
        assert "SpecialistAgent" in result.escalation_events[0]

    def test_triggered_guardrail_becomes_escalation_event(self):
        spans = [_agent_span(), _function_span("f", "sf", _T1), self._guardrail_span(triggered=True)]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert any("PII filter" in e for e in result.escalation_events)

    def test_untriggered_guardrail_not_escalated(self):
        spans = [_agent_span(), _function_span("f", "sf", _T1), self._guardrail_span(triggered=False)]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.escalation_events == []


# ---------------------------------------------------------------------------
# Error spans
# ---------------------------------------------------------------------------


class TestErrorSpans:
    _trace = {"trace_id": "trace-err", "name": "Workflow"}

    def test_error_span_captured_in_error_events(self):
        spans = [
            _agent_span(),
            _function_span(
                "bad_call", "span-err", _T1,
                **{"error": {"message": "Upstream timeout"}}
            ),
        ]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert len(result.error_events) == 1
        assert "bad_call" in result.error_events[0]
        assert "Upstream timeout" in result.error_events[0]

    def test_error_string_in_step_output(self):
        spans = [
            _agent_span(),
            _function_span(
                "bad_call", "span-err", _T1,
                **{"error": "Timed out", "span_data": {
                    "type": "function", "name": "bad_call", "input": "{}", "output": None,
                }}
            ),
        ]
        result = from_openai_agent_spans(
            self._trace, spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        step = result.steps[0]
        assert "Timed out" in str(step.output)


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------


class TestSpanGuards:
    _trace = {"trace_id": "t", "name": "W"}

    def test_empty_spans_raises(self):
        with pytest.raises(ValueError, match="empty"):
            from_openai_agent_spans(self._trace, [], agent_owner=_OWNER, submitted_by=_SUBMITTER)

    def test_only_agent_span_raises(self):
        with pytest.raises(ValueError, match="no function or llm spans"):
            from_openai_agent_spans(
                self._trace, [_agent_span()], agent_owner=_OWNER, submitted_by=_SUBMITTER
            )

    def test_self_evaluation_raises(self):
        with pytest.raises(Exception, match="self-evaluation"):
            from_openai_agent_spans(
                self._trace,
                _two_span_list(),
                agent_owner="Acme Corp",
                submitted_by="Acme Corp",
            )


# ---------------------------------------------------------------------------
# Trace string shorthand
# ---------------------------------------------------------------------------


def test_trace_id_string_shorthand():
    spans = _two_span_list()
    result = from_openai_agent_spans(
        "my-trace-id", spans, agent_owner=_OWNER, submitted_by=_SUBMITTER
    )
    assert result.trace_id == "my-trace-id"


# ---------------------------------------------------------------------------
# SDK mock objects for from_openai_agent_spans
# ---------------------------------------------------------------------------


class TestSdkObjects:
    def _make_sdk_span(
        self,
        span_id: str,
        parent_id: str | None,
        data_type: str,
        name: str,
        started_at: str,
        input_: Any = None,
        output_: Any = None,
        error: Any = None,
    ) -> MagicMock:
        span = MagicMock()
        span.trace_id = "trace-sdk"
        span.span_id = span_id
        span.parent_id = parent_id
        span.started_at = started_at
        span.ended_at = started_at
        span.error = error

        sd = MagicMock()
        sd.__class__.__name__ = f"{data_type.capitalize()}SpanData"
        sd.name = name
        sd.input = input_
        sd.output = output_
        sd.model = "gpt-4o"
        sd.handoffs = []
        sd.tools = []
        sd.triggered = False
        span.span_data = sd

        return span

    def test_sdk_spans_produce_decision_trace(self):
        root = self._make_sdk_span("root", None, "agent", "MyAgent", _T0, output_="Done")
        child = self._make_sdk_span("c1", "root", "function", "lookup", _T1,
                                    input_='{"id": 1}', output_='{"ok": true}')
        trace = MagicMock()
        trace.trace_id = "trace-sdk"
        trace.name = "SDKWorkflow"

        result = from_openai_agent_spans(
            trace, [root, child], agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert isinstance(result, DecisionTrace)
        assert result.agent_id == "MyAgent"
        assert result.steps[0].tool == "lookup"
        assert result.steps[0].input == {"id": 1}

    def test_sdk_llm_span_included(self):
        root = self._make_sdk_span("root", None, "agent", "Agent", _T0, output_="ok")
        llm = self._make_sdk_span("llm1", "root", "llm", "gpt-4o", _T1,
                                   input_=[{"role": "user", "content": "test"}])
        result = from_openai_agent_spans(
            "sdk-trace", [root, llm], agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert result.steps[0].tool == "llm:gpt-4o"

    def test_sdk_error_span(self):
        err = MagicMock()
        err.message = "API failure"
        root = self._make_sdk_span("root", None, "agent", "Agent", _T0, output_="")
        child = self._make_sdk_span("c1", "root", "function", "bad_tool", _T1, error=err)

        result = from_openai_agent_spans(
            "t", [root, child], agent_owner=_OWNER, submitted_by=_SUBMITTER
        )
        assert any("bad_tool" in e for e in result.error_events)


# ---------------------------------------------------------------------------
# from_run_result — dict form
# ---------------------------------------------------------------------------


def _tool_call_item(call_id: str, fn_name: str, args: str) -> dict:
    return {
        "type": "tool_call",
        "raw_item": {"id": call_id, "function": {"name": fn_name, "arguments": args}},
    }


def _tool_output_item(call_id: str, output: str) -> dict:
    return {
        "type": "tool_call_output",
        "output": output,
        "raw_item": {"id": call_id},
    }


def _handoff_item(src: str, dst: str) -> dict:
    return {
        "type": "handoff",
        "source_agent": SimpleNamespace(name=src),
        "target_agent": SimpleNamespace(name=dst),
    }


def _run_result(**overrides) -> dict:
    base = {
        "input": "Process claim XYZ",
        "final_output": "Claim approved.",
        "last_agent": SimpleNamespace(name="ClaimsAgent"),
        "new_items": [
            _tool_call_item("call-1", "eligibility_check", '{"member": "M001"}'),
            _tool_output_item("call-1", '{"status": "eligible"}'),
            _tool_call_item("call-2", "criteria_lookup", '{"cpt": "72148"}'),
            _tool_output_item("call-2", '{"result": "Criteria met"}'),
        ],
    }
    base.update(overrides)
    return base


class TestFromRunResult:
    def test_returns_decision_trace(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)

    def test_agent_id_from_last_agent(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_id == "ClaimsAgent"

    def test_two_steps_from_tool_calls(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 2

    def test_step_tool_names(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "eligibility_check"
        assert result.steps[1].tool == "criteria_lookup"

    def test_step_inputs_parsed_from_json(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].input == {"member": "M001"}

    def test_step_outputs_paired_by_call_id(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[1].output == {"result": "Criteria met"}

    def test_task_description_from_input(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "Process claim XYZ" in result.task_description

    def test_final_output_from_run_result(self):
        result = from_run_result(_run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "approved" in result.final_output.lower()

    def test_task_description_override(self):
        result = from_run_result(
            _run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER,
            task_description="Custom task"
        )
        assert result.task_description == "Custom task"

    def test_handoff_becomes_escalation_event(self):
        rr = _run_result()
        rr["new_items"].append(_handoff_item("TriageAgent", "SpecialistAgent"))
        result = from_run_result(rr, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert any("TriageAgent" in e for e in result.escalation_events)

    def test_no_tool_calls_raises(self):
        rr = {"input": "task", "final_output": "done", "last_agent": None, "new_items": []}
        with pytest.raises(ValueError, match="no tool-call steps"):
            from_run_result(rr, agent_owner=_OWNER, submitted_by=_SUBMITTER)

    def test_self_evaluation_guard_fires(self):
        with pytest.raises(Exception, match="self-evaluation"):
            from_run_result(_run_result(), agent_owner="Acme Corp", submitted_by="Acme Corp")

    def test_domain_profile_set(self):
        result = from_run_result(
            _run_result(), agent_owner=_OWNER, submitted_by=_SUBMITTER,
            domain_profile="financial_trading"
        )
        assert result.domain_profile == "financial_trading"


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestDetectSpanDataType:
    def test_dict_with_type_key(self):
        assert _detect_span_data_type({"type": "function", "name": "foo"}) == "function"

    def test_sdk_object_by_class_name(self):
        obj = MagicMock()
        obj.__class__.__name__ = "FunctionSpanData"
        assert _detect_span_data_type(obj) == "function"

    def test_llm_span_data_detected(self):
        obj = MagicMock()
        obj.__class__.__name__ = "LLMSpanData"
        assert _detect_span_data_type(obj) == "llm"

    def test_agent_span_data_detected(self):
        obj = MagicMock()
        obj.__class__.__name__ = "AgentSpanData"
        assert _detect_span_data_type(obj) == "agent"

    def test_none_returns_unknown(self):
        assert _detect_span_data_type(None) == "unknown"


class TestParseJsonStr:
    def test_valid_json_object(self):
        assert _parse_json_str('{"key": "value"}') == {"key": "value"}

    def test_valid_json_array(self):
        assert _parse_json_str("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_returns_raw(self):
        assert _parse_json_str("not json") == "not json"

    def test_empty_string_returns_empty_string(self):
        assert _parse_json_str("") == ""


class TestIsoToNs:
    def test_iso_string_converts(self):
        ns = _iso_to_ns("2026-07-01T10:00:00Z")
        assert ns > 0

    def test_zero_on_none(self):
        assert _iso_to_ns(None) == 0

    def test_integer_passthrough(self):
        assert _iso_to_ns(12345) == 12345

    def test_invalid_string_returns_zero(self):
        assert _iso_to_ns("not-a-date") == 0


class TestItemType:
    def test_dict_with_type(self):
        assert _item_type({"type": "tool_call"}) == "tool_call"

    def test_sdk_tool_call_item(self):
        obj = MagicMock()
        obj.__class__.__name__ = "ToolCallItem"
        assert _item_type(obj) == "tool_call"

    def test_sdk_tool_call_output_item(self):
        obj = MagicMock()
        obj.__class__.__name__ = "ToolCallOutputItem"
        assert _item_type(obj) == "tool_call_output"

    def test_sdk_handoff_item(self):
        obj = MagicMock()
        obj.__class__.__name__ = "HandoffOutputItem"
        assert _item_type(obj) == "handoff"

    def test_unknown_type(self):
        obj = MagicMock()
        obj.__class__.__name__ = "SomeRandomClass"
        assert _item_type(obj) == "unknown"


class TestFindRoot:
    def _span(self, sid: str, parent: str | None, ts: int) -> dict:
        return {
            "span_id": sid, "parent_id": parent,
            "started_at_ns": ts, "data_type": "function",
            "trace_id": "t", "started_at": None, "error": None, "data": {},
        }

    def test_identifies_root_by_no_parent(self):
        spans = [self._span("root", None, 100), self._span("child", "root", 200)]
        root = _find_root(spans)
        assert root["span_id"] == "root"

    def test_prefers_agent_span_as_root(self):
        spans = [
            {**self._span("a", None, 100), "data_type": "function"},
            {**self._span("b", None, 100), "data_type": "agent"},
        ]
        root = _find_root(spans)
        assert root["span_id"] == "b"

    def test_fallback_to_earliest_when_all_have_parents(self):
        spans = [
            self._span("x", "orphan", 200),
            self._span("y", "orphan", 100),
        ]
        root = _find_root(spans)
        assert root["span_id"] == "y"
