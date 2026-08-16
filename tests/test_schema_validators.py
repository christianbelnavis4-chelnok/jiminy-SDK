"""Tests for field validators added to DecisionTrace and Step in Sprint 3A."""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from schema.trace_schema import (
    _MAX_EVENT_ITEM,
    _MAX_EVENT_LIST,
    _MAX_ID,
    _MAX_IDENTITY,
    _MAX_LONG_TEXT,
    _MAX_SHORT_TEXT,
    _MAX_SLUG,
    _MAX_STEP_IO_STR,
    DecisionTrace,
    Step,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_trace(**overrides):
    data = {
        "trace_id": "trace-001",
        "agent_id": "agent-007",
        "agent_owner": "Acme Insurance",
        "submitted_by": "State Regulatory Office",
        "task_description": "Approve prior authorisation for CPT 72148",
        "timestamp": "2026-07-01T10:00:00Z",
        "domain_profile": "health_insurance_prior_auth",
        "steps": [
            {
                "step_id": 1,
                "tool": "eligibility_check",
                "input": {"member_id": "M001"},
                "output": {"status": "Active"},
                "reasoning": "Confirmed eligibility.",
            }
        ],
        "final_output": "Approved",
    }
    data.update(overrides)
    return data


def _base_step(**overrides):
    data = {
        "step_id": 1,
        "tool": "eligibility_check",
        "input": {"member_id": "M001"},
        "output": {"status": "Active"},
        "reasoning": "Confirmed eligibility.",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Step.tool validation
# ---------------------------------------------------------------------------


class TestStepTool:
    def test_valid_tool_accepted(self):
        step = Step(**_base_step())
        assert step.tool == "eligibility_check"

    def test_tool_whitespace_stripped(self):
        step = Step(**_base_step(tool="  my_tool  "))
        assert step.tool == "my_tool"

    def test_tool_empty_string_raises(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            Step(**_base_step(tool=""))

    def test_tool_whitespace_only_raises(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            Step(**_base_step(tool="   "))

    def test_tool_over_max_length_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            Step(**_base_step(tool="x" * (_MAX_SLUG + 1)))

    def test_tool_at_max_length_accepted(self):
        step = Step(**_base_step(tool="x" * _MAX_SLUG))
        assert len(step.tool) == _MAX_SLUG


# ---------------------------------------------------------------------------
# Step.reasoning validation
# ---------------------------------------------------------------------------


class TestStepReasoning:
    def test_none_reasoning_accepted(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            step = Step(**_base_step(reasoning=None))
        assert step.reasoning is None

    def test_valid_reasoning_accepted(self):
        step = Step(**_base_step(reasoning="Confirming active membership."))
        assert step.reasoning == "Confirming active membership."

    def test_reasoning_whitespace_stripped(self):
        step = Step(**_base_step(reasoning="  ok  "))
        assert step.reasoning == "ok"

    def test_reasoning_empty_string_raises(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            Step(**_base_step(reasoning=""))

    def test_reasoning_over_max_length_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            Step(**_base_step(reasoning="x" * (_MAX_LONG_TEXT + 1)))


# ---------------------------------------------------------------------------
# Step.input / Step.output string length
# ---------------------------------------------------------------------------


class TestStepIO:
    def test_dict_input_accepted(self):
        step = Step(**_base_step(input={"key": "value"}))
        assert step.input == {"key": "value"}

    def test_string_input_accepted(self):
        step = Step(**_base_step(input="some raw text"))
        assert step.input == "some raw text"

    def test_string_output_at_max_accepted(self):
        step = Step(**_base_step(output="x" * _MAX_STEP_IO_STR))
        assert len(step.output) == _MAX_STEP_IO_STR

    def test_string_input_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            Step(**_base_step(input="x" * (_MAX_STEP_IO_STR + 1)))

    def test_string_output_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            Step(**_base_step(output="x" * (_MAX_STEP_IO_STR + 1)))

    def test_non_string_input_not_validated_for_length(self):
        step = Step(**_base_step(input={"nested": {"deep": "value"}}))
        assert step.input == {"nested": {"deep": "value"}}


# ---------------------------------------------------------------------------
# DecisionTrace string field validators
# ---------------------------------------------------------------------------


class TestDecisionTraceIDs:
    def test_trace_id_whitespace_stripped(self):
        trace = DecisionTrace.model_validate(_base_trace(trace_id="  trace-001  "))
        assert trace.trace_id == "trace-001"

    def test_trace_id_empty_raises(self):
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(_base_trace(trace_id=""))

    def test_trace_id_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(_base_trace(trace_id="x" * (_MAX_ID + 1)))

    def test_agent_id_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(_base_trace(agent_id="a" * (_MAX_ID + 1)))

    def test_agent_id_whitespace_stripped(self):
        trace = DecisionTrace.model_validate(_base_trace(agent_id="  PA-007  "))
        assert trace.agent_id == "PA-007"


class TestDecisionTraceIdentity:
    def test_agent_owner_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(_base_trace(agent_owner="A" * (_MAX_IDENTITY + 1)))

    def test_submitted_by_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(_base_trace(submitted_by="S" * (_MAX_IDENTITY + 1)))

    def test_agent_owner_whitespace_stripped(self):
        trace = DecisionTrace.model_validate(_base_trace(agent_owner="  Acme  "))
        assert trace.agent_owner == "Acme"

    def test_submitted_by_empty_raises(self):
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(_base_trace(submitted_by=""))


class TestDecisionTraceTextFields:
    def test_task_description_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(
                _base_trace(task_description="x" * (_MAX_SHORT_TEXT + 1))
            )

    def test_task_description_at_max_accepted(self):
        trace = DecisionTrace.model_validate(
            _base_trace(task_description="x" * _MAX_SHORT_TEXT)
        )
        assert len(trace.task_description) == _MAX_SHORT_TEXT

    def test_final_output_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(
                _base_trace(final_output="x" * (_MAX_LONG_TEXT + 1))
            )

    def test_domain_profile_over_max_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            DecisionTrace.model_validate(
                _base_trace(domain_profile="x" * (_MAX_SLUG + 1))
            )

    def test_domain_profile_whitespace_stripped(self):
        trace = DecisionTrace.model_validate(
            _base_trace(domain_profile="  health_insurance_prior_auth  ")
        )
        assert trace.domain_profile == "health_insurance_prior_auth"

    def test_final_output_empty_raises(self):
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(_base_trace(final_output="   "))


# ---------------------------------------------------------------------------
# DecisionTrace event list validation
# ---------------------------------------------------------------------------


class TestDecisionTraceEventLists:
    def test_valid_escalation_events_accepted(self):
        trace = DecisionTrace.model_validate(
            _base_trace(escalation_events=["Escalated to human reviewer"])
        )
        assert trace.escalation_events == ["Escalated to human reviewer"]

    def test_event_items_whitespace_stripped(self):
        trace = DecisionTrace.model_validate(
            _base_trace(error_events=["  connection timeout  "])
        )
        assert trace.error_events == ["connection timeout"]

    def test_event_list_over_max_items_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum"):
            DecisionTrace.model_validate(
                _base_trace(escalation_events=["event"] * (_MAX_EVENT_LIST + 1))
            )

    def test_event_list_at_max_items_accepted(self):
        trace = DecisionTrace.model_validate(
            _base_trace(escalation_events=["event"] * _MAX_EVENT_LIST)
        )
        assert len(trace.escalation_events) == _MAX_EVENT_LIST

    def test_event_item_over_max_length_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum"):
            DecisionTrace.model_validate(
                _base_trace(error_events=["x" * (_MAX_EVENT_ITEM + 1)])
            )

    def test_non_string_event_item_raises(self):
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(
                _base_trace(escalation_events=[123])
            )

    def test_non_list_event_raises(self):
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(
                _base_trace(escalation_events="not-a-list")
            )


# ---------------------------------------------------------------------------
# Independence guard still works after adding validators
# ---------------------------------------------------------------------------


class TestIndependenceGuardUnchanged:
    def test_self_evaluation_still_raises(self):
        with pytest.raises(ValidationError, match="self-evaluation"):
            DecisionTrace.model_validate(
                _base_trace(agent_owner="Acme Corp", submitted_by="Acme Corp")
            )

    def test_independent_parties_still_accepted(self):
        trace = DecisionTrace.model_validate(
            _base_trace(agent_owner="Acme Corp", submitted_by="Regulator Inc")
        )
        assert trace.agent_owner == "Acme Corp"
        assert trace.submitted_by == "Regulator Inc"


# ---------------------------------------------------------------------------
# Self-serve SDK metadata: environment / framework
# ---------------------------------------------------------------------------


class TestSelfServeMetadataFields:
    def test_environment_defaults_to_production(self):
        trace = DecisionTrace.model_validate(_base_trace())
        assert trace.environment == "production"

    def test_environment_test_accepted(self):
        trace = DecisionTrace.model_validate(_base_trace(environment="test"))
        assert trace.environment == "test"

    def test_environment_invalid_value_raises(self):
        with pytest.raises(ValidationError, match="'test' or 'production'"):
            DecisionTrace.model_validate(_base_trace(environment="staging"))

    def test_framework_defaults_to_none(self):
        trace = DecisionTrace.model_validate(_base_trace())
        assert trace.framework is None

    def test_framework_accepts_free_text(self):
        trace = DecisionTrace.model_validate(_base_trace(framework="langchain"))
        assert trace.framework == "langchain"

    def test_framework_over_max_length_raises(self):
        with pytest.raises(ValidationError, match="exceeds maximum"):
            DecisionTrace.model_validate(_base_trace(framework="x" * (_MAX_SLUG + 1)))

    def test_source_is_not_a_trace_field(self):
        # `source` must never be settable from trace content — it is derived
        # server-side from the submitting API key's tier (see
        # api/helpers.py's persistence path). Confirm the schema has no such
        # field, so a client-supplied "source" key is silently ignored by
        # Pydantic rather than accepted.
        assert "source" not in DecisionTrace.model_fields
