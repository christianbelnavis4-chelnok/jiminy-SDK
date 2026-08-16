"""Tests for schema/trace_schema.py — DecisionTrace and Step models."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema.trace_schema import DecisionTrace, Step

TRACES_DIR = Path(__file__).resolve().parent.parent / "traces"


class TestDecisionTrace:
    def test_approval_trace_parses(self):
        data = json.loads((TRACES_DIR / "trace_01_approval.json").read_text())
        trace = DecisionTrace.model_validate(data)
        assert trace.trace_id == "a1b2c3d4-0001-4e5f-8a9b-c0d1e2f3a4b5"
        assert trace.agent_id == "PA-Agent-07"
        assert trace.agent_owner == "NationalHealth Insurance Co."
        assert trace.submitted_by == "sample-submitter"
        assert len(trace.steps) == 3
        assert trace.final_output.startswith("Approved")
        assert trace.escalation_events == []
        assert trace.error_events == []

    def test_escalation_trace_parses(self):
        data = json.loads((TRACES_DIR / "trace_03_escalation.json").read_text())
        trace = DecisionTrace.model_validate(data)
        assert trace.trace_id == "c3d4e5f6-0003-4a7b-0c1d-e2f3a4b5c6d7"
        assert len(trace.steps) == 3
        assert len(trace.escalation_events) == 1
        assert "Investigational" in trace.escalation_events[0]
        assert trace.error_events == []

    def test_sample_trace_fixture_is_valid(self, sample_trace):
        trace = DecisionTrace.model_validate(sample_trace)
        assert trace.agent_id == "PA-Agent-07"
        assert len(trace.steps) == 1
        assert trace.domain_profile == "health_insurance_prior_auth"
        assert trace.agent_owner == "NationalHealth Insurance Co."
        assert trace.submitted_by == "State Insurance Commissioner Office"

    def test_missing_trace_id_raises(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "trace_id"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        assert any(e["loc"] == ("trace_id",) for e in exc_info.value.errors())

    def test_missing_timestamp_raises(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "timestamp"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        assert any(e["loc"] == ("timestamp",) for e in exc_info.value.errors())

    def test_invalid_timestamp_raises(self, sample_trace):
        data = {**sample_trace, "timestamp": "not-a-date"}
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(data)

    def test_missing_domain_profile_raises(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "domain_profile"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        assert any(e["loc"] == ("domain_profile",) for e in exc_info.value.errors())

    def test_empty_steps_raises(self, sample_trace):
        data = {**sample_trace, "steps": []}
        with pytest.raises(ValidationError):
            DecisionTrace.model_validate(data)

    def test_escalation_events_default_to_empty_list(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "escalation_events"}
        trace = DecisionTrace.model_validate(data)
        assert trace.escalation_events == []

    def test_agent_owner_required(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "agent_owner"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        assert any(e["loc"] == ("agent_owner",) for e in exc_info.value.errors())

    def test_submitted_by_required(self, sample_trace):
        data = {k: v for k, v in sample_trace.items() if k != "submitted_by"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        assert any(e["loc"] == ("submitted_by",) for e in exc_info.value.errors())

    def test_self_evaluation_raises(self, sample_trace):
        data = {**sample_trace, "agent_owner": "Same Org", "submitted_by": "Same Org"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        errors = exc_info.value.errors()
        assert any("self-evaluation" in str(e["msg"]) for e in errors)

    def test_self_evaluation_error_message(self, sample_trace):
        data = {**sample_trace, "agent_owner": "Acme Corp", "submitted_by": "Acme Corp"}
        with pytest.raises(ValidationError) as exc_info:
            DecisionTrace.model_validate(data)
        error_text = str(exc_info.value)
        assert "independent" in error_text.lower()

    def test_different_owner_and_submitter_is_valid(self, sample_trace):
        data = {
            **sample_trace,
            "agent_owner": "Insurance Co.",
            "submitted_by": "Regulatory Body",
        }
        trace = DecisionTrace.model_validate(data)
        assert trace.agent_owner == "Insurance Co."
        assert trace.submitted_by == "Regulatory Body"


class TestStep:
    def test_step_with_reasoning_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Step(
                step_id=1,
                tool="eligibility_check",
                input={"member_id": "88213-A"},
                output={"status": "Active"},
                reasoning="Confirming eligibility before proceeding.",
            )
        jiminy_warnings = [w for w in caught if "[JIMINY WARNING]" in str(w.message)]
        assert jiminy_warnings == []

    def test_step_missing_reasoning_emits_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Step(
                step_id=2,
                tool="clinical_criteria_lookup",
                input={"cpt_code": "72148"},
                output={"result": "Criteria met"},
                reasoning=None,
            )
        jiminy_warnings = [w for w in caught if "[JIMINY WARNING]" in str(w.message)]
        assert len(jiminy_warnings) == 1
        assert "Step 2" in str(jiminy_warnings[0].message)
        assert "clinical_criteria_lookup" in str(jiminy_warnings[0].message)

    def test_step_missing_reasoning_field_defaults_to_none(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            step = Step(
                step_id=1,
                tool="eligibility_check",
                input={},
                output={},
            )
        assert step.reasoning is None

    def test_step_missing_tool_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Step(step_id=1, input={}, output={})
        assert any(e["loc"] == ("tool",) for e in exc_info.value.errors())

    def test_step_accepts_string_input_output(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            step = Step(
                step_id=3,
                tool="cost_variance_model",
                input="claim_value: $4200, regional_avg: $2850",
                output="Above regional average threshold",
                reasoning="Used cost variance as denial signal.",
            )
        assert step.input == "claim_value: $4200, regional_avg: $2850"
        assert step.output == "Above regional average threshold"
