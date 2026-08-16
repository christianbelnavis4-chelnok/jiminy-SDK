"""Tests for adapters/crewai/adapter.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adapters.crewai.adapter import (
    _derive_task_description,
    _detect_handoffs,
    _extract_final_output,
    _extract_task_output,
    from_crew_output,
)
from schema.trace_schema import DecisionTrace

_OWNER = "Acme Insurance"
_SUBMITTER = "State Regulatory Office"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _task(
    description: str,
    agent: str,
    raw: str,
    name: str | None = None,
    summary: str | None = None,
    json_dict: dict | None = None,
) -> dict:
    return {
        "description": description,
        "name": name,
        "agent": agent,
        "raw": raw,
        "summary": summary,
        "json_dict": json_dict,
        "expected_output": None,
        "pydantic": None,
    }


def _crew_output(**overrides) -> dict:
    base = {
        "raw": "Prior authorisation approved for CPT 72148.",
        "tasks_output": [
            _task(
                "Check member eligibility for CPT 72148",
                "EligibilityAgent",
                "Member M001 is active and eligible.",
                name="eligibility_task",
            ),
            _task(
                "Assess clinical criteria for CPT 72148",
                "ClinicalAgent",
                "Criteria met under policy section 4.2.",
                name="criteria_task",
            ),
        ],
        "token_usage": {"total_tokens": 820},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# from_crew_output — happy path
# ---------------------------------------------------------------------------


class TestFromCrewOutputHappyPath:
    def test_returns_decision_trace(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)

    def test_agent_id_from_first_task_agent(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_id == "EligibilityAgent"

    def test_agent_owner_and_submitter_set(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_owner == _OWNER
        assert result.submitted_by == _SUBMITTER

    def test_two_steps_from_two_tasks(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 2

    def test_step_ids_sequential(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert [s.step_id for s in result.steps] == [1, 2]

    def test_step_tool_is_agent_role(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "EligibilityAgent"
        assert result.steps[1].tool == "ClinicalAgent"

    def test_step_input_is_task_description(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "eligibility" in result.steps[0].input.lower()

    def test_step_output_is_task_raw(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "eligible" in result.steps[0].output.lower()

    def test_final_output_from_crew_raw(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "approved" in result.final_output.lower()

    def test_task_description_derived_from_tasks(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "eligibility" in result.task_description.lower() or "criteria" in result.task_description.lower()

    def test_task_description_override(self):
        result = from_crew_output(
            _crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER,
            task_description="Custom description"
        )
        assert result.task_description == "Custom description"

    def test_trace_id_override(self):
        result = from_crew_output(
            _crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER,
            trace_id="my-run-001"
        )
        assert result.trace_id == "my-run-001"

    def test_trace_id_generated_when_omitted(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.trace_id) > 0

    def test_domain_profile_default_general(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.domain_profile == "general"

    def test_domain_profile_override(self):
        result = from_crew_output(
            _crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER,
            domain_profile="health_insurance_prior_auth"
        )
        assert result.domain_profile == "health_insurance_prior_auth"

    def test_no_error_events(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.error_events == []


# ---------------------------------------------------------------------------
# Handoff (escalation) events
# ---------------------------------------------------------------------------


class TestHandoffDetection:
    def test_different_agents_creates_escalation_event(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.escalation_events) == 1
        assert "EligibilityAgent" in result.escalation_events[0]
        assert "ClinicalAgent" in result.escalation_events[0]
        assert "→" in result.escalation_events[0]

    def test_same_agent_no_escalation(self):
        co = _crew_output()
        co["tasks_output"] = [
            _task("Task A", "SameAgent", "Output A"),
            _task("Task B", "SameAgent", "Output B"),
        ]
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.escalation_events == []

    def test_three_agents_two_escalations(self):
        co = _crew_output()
        co["tasks_output"] = [
            _task("Step 1", "AgentA", "output"),
            _task("Step 2", "AgentB", "output"),
            _task("Step 3", "AgentC", "output"),
        ]
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.escalation_events) == 2

    def test_handoff_back_counts_as_escalation(self):
        co = _crew_output()
        co["tasks_output"] = [
            _task("Step 1", "AgentA", "output"),
            _task("Step 2", "AgentB", "output"),
            _task("Step 3", "AgentA", "output"),  # back to A
        ]
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.escalation_events) == 2

    def test_single_task_no_escalation(self):
        co = _crew_output()
        co["tasks_output"] = [_task("Only task", "OnlyAgent", "output")]
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.escalation_events == []


# ---------------------------------------------------------------------------
# Structured output (json_dict)
# ---------------------------------------------------------------------------


class TestStructuredOutput:
    def test_json_dict_used_as_step_output_when_present(self):
        co = _crew_output()
        co["tasks_output"][0]["json_dict"] = {"status": "eligible", "score": 0.95}
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].output == {"status": "eligible", "score": 0.95}

    def test_raw_used_when_json_dict_absent(self):
        result = from_crew_output(_crew_output(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result.steps[0].output, str)
        assert "eligible" in result.steps[0].output.lower()

    def test_summary_used_as_fallback_when_raw_empty(self):
        co = _crew_output()
        co["tasks_output"][0]["raw"] = ""
        co["tasks_output"][0]["summary"] = "Eligible summary"
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "Eligible summary" in result.steps[0].output


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_empty_tasks_output_raises(self):
        with pytest.raises(ValueError, match="tasks_output is empty"):
            from_crew_output(
                {"raw": "done", "tasks_output": []},
                agent_owner=_OWNER, submitted_by=_SUBMITTER
            )

    def test_missing_tasks_output_raises(self):
        with pytest.raises(ValueError, match="tasks_output is empty"):
            from_crew_output(
                {"raw": "done"},
                agent_owner=_OWNER, submitted_by=_SUBMITTER
            )

    def test_self_evaluation_guard_fires(self):
        with pytest.raises(Exception, match="self-evaluation"):
            from_crew_output(
                _crew_output(), agent_owner="Acme Corp", submitted_by="Acme Corp"
            )


# ---------------------------------------------------------------------------
# SDK object duck-typing
# ---------------------------------------------------------------------------


class TestSdkObjectDuckTyping:
    def _make_task_output(self, description: str, agent: str, raw: str) -> MagicMock:
        t = MagicMock()
        t.description = description
        t.name = None
        t.agent = agent
        t.raw = raw
        t.summary = None
        t.json_dict = None
        t.pydantic = None
        t.expected_output = None
        # Ensure model_dump and dict are NOT accidentally available
        del t.model_dump
        del t.dict
        return t

    def _make_crew_output(self, raw: str, tasks: list) -> MagicMock:
        co = MagicMock()
        co.raw = raw
        co.tasks_output = tasks
        co.token_usage = {"total_tokens": 100}
        co.pydantic = None
        co.json_dict = None
        del co.model_dump
        del co.dict
        return co

    def test_sdk_objects_produce_valid_trace(self):
        tasks = [
            self._make_task_output("Check eligibility", "EligibilityAgent", "Member eligible"),
            self._make_task_output("Assess criteria", "ClinicalAgent", "Criteria met"),
        ]
        co = self._make_crew_output("Approved.", tasks)
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)
        assert result.agent_id == "EligibilityAgent"
        assert len(result.steps) == 2

    def test_sdk_handoff_detected(self):
        tasks = [
            self._make_task_output("Step A", "AgentA", "done"),
            self._make_task_output("Step B", "AgentB", "done"),
        ]
        co = self._make_crew_output("Final output", tasks)
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.escalation_events) == 1
        assert "AgentA" in result.escalation_events[0]

    def test_sdk_model_dump_used_when_available(self):
        co = MagicMock()
        co.model_dump.return_value = {
            "raw": "Result",
            "tasks_output": [
                {"description": "do task", "agent": "Agent1", "raw": "done",
                 "name": None, "summary": None, "json_dict": None, "pydantic": None},
            ],
        }
        result = from_crew_output(co, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_id == "Agent1"


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestDetectHandoffs:
    def test_two_different_agents(self):
        tasks = [
            {"agent": "A", "description": "t1", "raw": "out"},
            {"agent": "B", "description": "t2", "raw": "out"},
        ]
        events = _detect_handoffs(tasks)
        assert len(events) == 1
        assert "A" in events[0] and "B" in events[0]

    def test_same_agent_no_event(self):
        tasks = [
            {"agent": "A", "description": "t1", "raw": "out"},
            {"agent": "A", "description": "t2", "raw": "out"},
        ]
        assert _detect_handoffs(tasks) == []

    def test_single_task_no_event(self):
        assert _detect_handoffs([{"agent": "A", "description": "t", "raw": "out"}]) == []


class TestDeriveTaskDescription:
    def test_single_task_uses_description(self):
        tasks = [{"description": "Check eligibility", "agent": "A", "raw": "out"}]
        assert _derive_task_description(tasks) == "Check eligibility"

    def test_multiple_tasks_joined(self):
        tasks = [
            {"description": "Task one", "agent": "A", "raw": "out"},
            {"description": "Task two", "agent": "B", "raw": "out"},
        ]
        result = _derive_task_description(tasks)
        assert "Task one" in result
        assert "Task two" in result

    def test_empty_descriptions_fallback(self):
        tasks = [{"description": None, "agent": "A", "raw": "out"}]
        assert _derive_task_description(tasks) == "Multi-agent crew run"


class TestExtractTaskOutput:
    def test_prefers_json_dict(self):
        task = {"json_dict": {"key": "val"}, "raw": "raw text", "summary": None}
        assert _extract_task_output(task) == {"key": "val"}

    def test_falls_back_to_raw(self):
        task = {"json_dict": None, "raw": "raw text", "summary": None}
        assert _extract_task_output(task) == "raw text"

    def test_falls_back_to_summary(self):
        task = {"json_dict": None, "raw": "", "summary": "short summary"}
        assert _extract_task_output(task) == "short summary"

    def test_empty_json_dict_falls_back_to_raw(self):
        task = {"json_dict": {}, "raw": "raw text", "summary": None}
        assert _extract_task_output(task) == "raw text"


class TestExtractFinalOutput:
    def test_crew_raw_used(self):
        raw = {"raw": "Crew final output", "tasks_output": []}
        assert _extract_final_output(raw) == "Crew final output"

    def test_falls_back_to_last_task_output(self):
        raw = {
            "raw": "",
            "tasks_output": [
                {"description": "t", "agent": "A", "raw": "first task"},
                {"description": "t2", "agent": "A", "raw": "last task output"},
            ],
        }
        assert "last task output" in _extract_final_output(raw)

    def test_no_output_placeholder(self):
        assert _extract_final_output({"raw": "", "tasks_output": []}) == "No output"
