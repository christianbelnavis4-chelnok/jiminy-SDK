"""Tests for adapters/langsmith/adapter.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adapters.langsmith.adapter import (
    _as_dict,
    _extract_output,
    _extract_task,
    _parse_timestamp,
    from_langsmith_run,
)
from schema.trace_schema import DecisionTrace

# ---------------------------------------------------------------------------
# Minimal run fixtures
# ---------------------------------------------------------------------------

def _run(**overrides) -> dict:
    """Minimal valid LangSmith-shaped run dict."""
    base = {
        "id": "run-001",
        "name": "AgentExecutor",
        "run_type": "chain",
        "inputs": {"input": "What is the prior auth status for CPT 72148?"},
        "outputs": {"output": "Prior auth approved."},
        "start_time": "2026-07-01T10:00:00Z",
        "end_time": "2026-07-01T10:00:05Z",
        "error": None,
        "child_runs": [
            {
                "id": "run-002",
                "name": "eligibility_check",
                "run_type": "tool",
                "inputs": {"member_id": "M001"},
                "outputs": {"status": "Active"},
                "error": None,
                "child_runs": [],
            },
            {
                "id": "run-003",
                "name": "criteria_lookup",
                "run_type": "tool",
                "inputs": {"cpt_code": "72148"},
                "outputs": {"result": "Criteria met"},
                "error": None,
                "child_runs": [],
            },
        ],
    }
    base.update(overrides)
    return base


_OWNER = "Acme Insurance"
_SUBMITTER = "State Regulatory Office"


# ---------------------------------------------------------------------------
# from_langsmith_run — happy path
# ---------------------------------------------------------------------------


class TestFromLangsmithRunHappyPath:
    def test_returns_decision_trace(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert isinstance(result, DecisionTrace)

    def test_trace_id_from_run_id(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.trace_id == "run-001"

    def test_agent_id_from_run_name(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_id == "AgentExecutor"

    def test_agent_owner_set(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.agent_owner == _OWNER

    def test_submitted_by_set(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.submitted_by == _SUBMITTER

    def test_two_tool_steps_extracted(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 2

    def test_step_ids_sequential(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert [s.step_id for s in result.steps] == [1, 2]

    def test_step_tool_names(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "eligibility_check"
        assert result.steps[1].tool == "criteria_lookup"

    def test_step_inputs_preserved(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].input == {"member_id": "M001"}

    def test_step_outputs_preserved(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[1].output == {"result": "Criteria met"}

    def test_task_description_from_input_key(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "prior auth" in result.task_description.lower()

    def test_final_output_from_output_key(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert "approved" in result.final_output.lower()

    def test_timestamp_parsed(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.timestamp.year == 2026

    def test_domain_profile_default(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.domain_profile == "general"

    def test_domain_profile_override(self):
        result = from_langsmith_run(
            _run(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            domain_profile="health_insurance_prior_auth",
        )
        assert result.domain_profile == "health_insurance_prior_auth"

    def test_task_description_override(self):
        result = from_langsmith_run(
            _run(),
            agent_owner=_OWNER,
            submitted_by=_SUBMITTER,
            task_description="Custom task description",
        )
        assert result.task_description == "Custom task description"

    def test_no_error_events_when_clean(self):
        result = from_langsmith_run(_run(), agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.error_events == []


# ---------------------------------------------------------------------------
# Error events
# ---------------------------------------------------------------------------


class TestErrorEvents:
    def test_child_error_becomes_error_event(self):
        run = _run()
        run["child_runs"][0]["error"] = "Connection refused"
        run["child_runs"][0]["outputs"] = {}
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.error_events) == 1
        assert "eligibility_check" in result.error_events[0]
        assert "Connection refused" in result.error_events[0]

    def test_multiple_errors_captured(self):
        run = _run()
        run["child_runs"][0]["error"] = "err-a"
        run["child_runs"][0]["outputs"] = {}
        run["child_runs"][1]["error"] = "err-b"
        run["child_runs"][1]["outputs"] = {}
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.error_events) == 2

    def test_error_step_output_contains_error_text(self):
        run = _run()
        run["child_runs"][0]["error"] = "Timeout"
        run["child_runs"][0]["outputs"] = {}
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        step = next(s for s in result.steps if s.tool == "eligibility_check")
        assert "Timeout" in str(step.output)


# ---------------------------------------------------------------------------
# Non-tool child runs are skipped
# ---------------------------------------------------------------------------


class TestNonToolRunsSkipped:
    def test_chain_child_run_skipped(self):
        run = _run()
        run["child_runs"].append({
            "id": "run-004",
            "name": "inner_chain",
            "run_type": "chain",
            "inputs": {},
            "outputs": {},
            "error": None,
            "child_runs": [],
        })
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 2  # chain child not included

    def test_only_non_tool_children_raises(self):
        run = _run(child_runs=[
            {
                "id": "run-x",
                "name": "inner_chain",
                "run_type": "chain",
                "inputs": {},
                "outputs": {},
                "error": None,
                "child_runs": [],
            }
        ])
        with pytest.raises(ValueError, match="no evaluatable child runs"):
            from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)

    def test_llm_run_type_included(self):
        run = _run(child_runs=[
            {
                "id": "run-llm",
                "name": "ChatOpenAI",
                "run_type": "llm",
                "inputs": {"messages": [["human", "Is this eligible?"]]},
                "outputs": {"generations": [["Yes"]]},
                "error": None,
                "child_runs": [],
            }
        ])
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert len(result.steps) == 1
        assert result.steps[0].tool == "ChatOpenAI"

    def test_retriever_run_type_included(self):
        run = _run(child_runs=[
            {
                "id": "run-ret",
                "name": "VectorStoreRetriever",
                "run_type": "retriever",
                "inputs": {"query": "CPT 72148 criteria"},
                "outputs": {"documents": ["doc1"]},
                "error": None,
                "child_runs": [],
            }
        ])
        result = from_langsmith_run(run, agent_owner=_OWNER, submitted_by=_SUBMITTER)
        assert result.steps[0].tool == "VectorStoreRetriever"


# ---------------------------------------------------------------------------
# Self-evaluation guard still fires
# ---------------------------------------------------------------------------


class TestSelfEvaluationGuard:
    def test_same_owner_and_submitter_raises(self):
        with pytest.raises(Exception, match="self-evaluation"):
            from_langsmith_run(
                _run(),
                agent_owner="Acme Corp",
                submitted_by="Acme Corp",
            )


# ---------------------------------------------------------------------------
# _as_dict — duck-typing support
# ---------------------------------------------------------------------------


class TestAsDict:
    def test_dict_passthrough(self):
        d = {"id": "x", "name": "agent"}
        assert _as_dict(d) is d

    def test_model_dump_used(self):
        class FakeRun:
            def model_dump(self):
                return {"id": "y", "name": "agent2"}

        assert _as_dict(FakeRun()) == {"id": "y", "name": "agent2"}

    def test_dict_method_used_as_fallback(self):
        class OldRun:
            def dict(self):
                return {"id": "z", "name": "agent3"}

        assert _as_dict(OldRun()) == {"id": "z", "name": "agent3"}

    def test_unknown_type_raises(self):
        with pytest.raises(TypeError):
            _as_dict(42)


# ---------------------------------------------------------------------------
# _parse_timestamp edge cases
# ---------------------------------------------------------------------------


class TestParseTimestamp:
    def test_none_returns_utc_now(self):
        ts = _parse_timestamp(None)
        assert ts.tzinfo is not None

    def test_datetime_with_tz(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert _parse_timestamp(dt) == dt

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 1)
        result = _parse_timestamp(dt)
        assert result.tzinfo == UTC

    def test_iso_string_parsed(self):
        result = _parse_timestamp("2026-07-01T10:00:00Z")
        assert result.year == 2026
        assert result.month == 7

    def test_iso_string_with_offset_parsed(self):
        result = _parse_timestamp("2026-07-01T10:00:00+01:00")
        assert result.year == 2026

    def test_unparseable_string_returns_now(self):
        result = _parse_timestamp("not-a-date")
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _extract_task / _extract_output fallbacks
# ---------------------------------------------------------------------------


class TestExtractTask:
    def test_input_key_used(self):
        raw = {"inputs": {"input": "What is the status?"}, "name": "Agent"}
        assert _extract_task(raw) == "What is the status?"

    def test_question_key_fallback(self):
        raw = {"inputs": {"question": "Is this eligible?"}, "name": "Agent"}
        assert _extract_task(raw) == "Is this eligible?"

    def test_name_fallback_when_no_inputs(self):
        raw = {"inputs": {}, "name": "MyAgent"}
        result = _extract_task(raw)
        assert "MyAgent" in result

    def test_empty_name_returns_generic(self):
        raw = {"inputs": {}, "name": ""}
        assert _extract_task(raw) == "Agent run"


class TestExtractOutput:
    def test_output_key_used(self):
        raw = {"outputs": {"output": "Approved"}}
        assert _extract_output(raw) == "Approved"

    def test_result_key_fallback(self):
        raw = {"outputs": {"result": "Denied"}}
        assert _extract_output(raw) == "Denied"

    def test_error_fallback_when_no_outputs(self):
        raw = {"outputs": {}, "error": "LLM timeout"}
        result = _extract_output(raw)
        assert "LLM timeout" in result

    def test_no_output_no_error_returns_placeholder(self):
        raw = {"outputs": {}, "error": None}
        assert _extract_output(raw) == "No output"
