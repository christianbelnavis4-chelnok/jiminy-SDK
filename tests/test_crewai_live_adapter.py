"""Tests for adapters/crewai/live.py — the automatic-evaluation CrewAI
event listener.

Unlike adapters/langchain (a handler object passed per-invocation),
CrewAI's event bus is a process-global singleton — create_jiminy_event_listener
registers handlers on it directly. Every test here wraps registration and
emission in crewai_event_bus.scoped_handlers() so tests don't leak
handlers into each other (or interfere with CrewAI's own built-in
telemetry listeners, which error loudly — but harmlessly to our handlers
— on synthetic events missing fields a real Crew always provides).

Two non-obvious things this file's helpers exist because of, both found
by testing against a real crewai install rather than assumed from docs:

1. crewai_event_bus.emit() runs sync handlers on a background
   ThreadPoolExecutor and returns a Future — it does not block until
   handlers finish (see its own docstring: "future.result(timeout=5.0)
   in sync code"). Every emission here goes through _emit(), which waits.
2. ToolUsageFinishedEvent.started_event_id is NOT a caller-supplied
   correlation ID — CrewAI's _prepare_event() overwrites it by popping a
   process-global, ContextVar-tracked LIFO scope stack shared across every
   in-flight crew. Passing an explicit value for it is a no-op; the
   adapter under test deliberately doesn't rely on it (see
   adapters/crewai/live.py's module docstring for the full story), so
   these tests don't pass or assert on it either.

Requires crewai (pytest.importorskip — skipped, not failed, in any
environment where it isn't installed, since it's an optional dependency).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

crewai_events = pytest.importorskip("crewai.events")

from crewai.events import crewai_event_bus  # noqa: E402
from crewai.events.types.crew_events import (  # noqa: E402
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
)
from crewai.events.types.tool_usage_events import (  # noqa: E402
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

from adapters.crewai.live import create_jiminy_event_listener  # noqa: E402


def _crew(crew_id: str = "crew-1"):
    return SimpleNamespace(id=crew_id, share_crew=False)


def _register(**overrides):
    defaults = dict(
        api_key="k",
        base_url="https://api.example.com",
        agent_owner="Acme",
        submitted_by="tenant-1",
        domain_profile="general",
        async_submit=False,
    )
    defaults.update(overrides)
    create_jiminy_event_listener(**defaults)


class _FakeEvaluate:
    def __init__(self, verdict="approved"):
        self.calls: list[dict] = []
        self._verdict = verdict

    def __call__(self, trace, **kwargs):
        self.calls.append(trace)
        return {"overall_verdict": self._verdict}


def _emit(source, event):
    """Emit and wait for handler completion — see module docstring point 1."""
    future = crewai_event_bus.emit(source, event)
    if future is not None:
        future.result(timeout=5.0)
    return event


def _tool_start(source, tool_name="get_weather", args=None, task_id="t1"):
    _emit(
        source,
        ToolUsageStartedEvent(
            tool_name=tool_name, tool_args=args or {}, agent_key="a1", task_id=task_id
        ),
    )


def _tool_end(source, tool_name="get_weather", output="ok", task_id="t1"):
    _emit(
        source,
        ToolUsageFinishedEvent(
            tool_name=tool_name,
            tool_args={},
            agent_key="a1",
            task_id=task_id,
            output=output,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    )


def _tool_error(source, tool_name="get_weather", error="boom", task_id="t1"):
    _emit(
        source,
        ToolUsageErrorEvent(
            tool_name=tool_name, tool_args={}, agent_key="a1", task_id=task_id, error=error
        ),
    )


class TestSingleToolCall:
    def test_submits_trace_with_one_step(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(
                    source,
                    CrewKickoffStartedEvent(
                        crew_name="TestCrew", inputs={"topic": "weather in Paris"}
                    ),
                )
                _tool_start(source, args={"city": "Paris"})
                _tool_end(source, output="Sunny, 22C")
                _emit(
                    source,
                    CrewKickoffCompletedEvent(
                        crew_name="TestCrew", output="Sunny and 22C in Paris."
                    ),
                )

        assert len(fake.calls) == 1
        trace = fake.calls[0]
        assert trace["agent_owner"] == "Acme"
        assert trace["framework"] == "crewai"
        assert len(trace["steps"]) == 1
        assert trace["steps"][0]["tool"] == "get_weather"
        assert trace["steps"][0]["input"] == {"city": "Paris"}
        assert trace["steps"][0]["output"] == "Sunny, 22C"
        assert "trace_root_hash" in trace

    def test_no_tool_calls_skips_submission(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _emit(source, CrewKickoffCompletedEvent(crew_name="C", output="done"))
        assert fake.calls == []


class TestMultipleToolCallsOrdering:
    def test_sequential_tool_calls_pair_fifo(self):
        """Within one crew, tool calls are start-then-end, not overlapping
        — the realistic shape for a single agent working through a task
        list. Confirms FIFO pairing produces the right tool per step."""
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                for i in range(3):
                    _tool_start(source, tool_name=f"tool_{i}")
                    _tool_end(source, tool_name=f"tool_{i}", output=f"out_{i}")
                _emit(source, CrewKickoffCompletedEvent(crew_name="C", output="done"))

        steps = fake.calls[0]["steps"]
        assert [s["step_id"] for s in steps] == [1, 2, 3]
        assert [s["tool"] for s in steps] == ["tool_0", "tool_1", "tool_2"]
        assert [s["output"] for s in steps] == ["out_0", "out_1", "out_2"]

    def test_extra_finish_with_no_pending_start_is_ignored(self):
        """A stray finish event (no matching pending start for this
        source) must not create a bogus step or raise."""
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _tool_end(source, tool_name="orphan_end", output="o")  # no matching start
                _tool_start(source, tool_name="real_tool")
                _tool_end(source, tool_name="real_tool", output="ok")
                _emit(source, CrewKickoffCompletedEvent(crew_name="C", output="done"))

        steps = fake.calls[0]["steps"]
        assert len(steps) == 1
        assert steps[0]["tool"] == "real_tool"


class TestConcurrentCrews:
    def test_two_crews_do_not_mix_state(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                crew_a, crew_b = _crew("crew-a"), _crew("crew-b")
                _emit(crew_a, CrewKickoffStartedEvent(crew_name="A", inputs={"x": "A"}))
                _emit(crew_b, CrewKickoffStartedEvent(crew_name="B", inputs={"x": "B"}))
                _tool_start(crew_a, tool_name="tool_a")
                _tool_start(crew_b, tool_name="tool_b")
                _tool_end(crew_a, tool_name="tool_a", output="out_a")
                _tool_end(crew_b, tool_name="tool_b", output="out_b")
                _emit(crew_a, CrewKickoffCompletedEvent(crew_name="A", output="done_a"))
                _emit(crew_b, CrewKickoffCompletedEvent(crew_name="B", output="done_b"))

        assert len(fake.calls) == 2
        tools = sorted(c["steps"][0]["tool"] for c in fake.calls)
        assert tools == ["tool_a", "tool_b"]
        # Each trace's own tool paired with its own output, not the other crew's.
        for c in fake.calls:
            step = c["steps"][0]
            assert step["output"] == ("out_a" if step["tool"] == "tool_a" else "out_b")


class TestErrors:
    def test_tool_error_recorded_without_a_step(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _tool_start(source, tool_name="flaky_tool")
                _tool_error(source, tool_name="flaky_tool", error="boom")
                _tool_start(source, tool_name="ok_tool")
                _tool_end(source, tool_name="ok_tool", output="fine")
                _emit(source, CrewKickoffCompletedEvent(crew_name="C", output="recovered"))

        trace = fake.calls[0]
        assert len(trace["steps"]) == 1
        assert trace["steps"][0]["tool"] == "ok_tool"
        assert any("flaky_tool" in e for e in trace["error_events"])

    def test_kickoff_failed_still_submits_if_steps_exist(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _tool_start(source)
                _tool_end(source, output="ok")
                _emit(source, CrewKickoffFailedEvent(crew_name="C", error="agent crashed"))

        assert len(fake.calls) == 1
        assert "agent crashed" in fake.calls[0]["error_events"][0]


class TestAsyncSubmit:
    def test_async_submit_runs_in_background_thread(self):
        fake = _FakeEvaluate()
        done = threading.Event()

        def on_result(trace_id, result):
            done.set()

        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register(async_submit=True, on_result=on_result)
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _tool_start(source)
                _tool_end(source, output="ok")
                _emit(source, CrewKickoffCompletedEvent(crew_name="C", output="done"))
                assert done.wait(timeout=2), "async submission did not complete in time"

        assert len(fake.calls) == 1


class TestCrewOutputStringify:
    def test_final_output_uses_raw_attribute_when_present(self):
        fake = _FakeEvaluate()
        with crewai_event_bus.scoped_handlers():
            with patch("jiminy_sdk.client.Client.evaluate", fake):
                _register()
                source = _crew()
                _emit(source, CrewKickoffStartedEvent(crew_name="C", inputs={"x": "y"}))
                _tool_start(source)
                _tool_end(source, output="ok")
                fake_crew_output = SimpleNamespace(raw="The final answer text.")
                _emit(
                    source,
                    CrewKickoffCompletedEvent(crew_name="C", output=fake_crew_output),
                )

        assert fake.calls[0]["final_output"] == "The final answer text."


def test_missing_crewai_raises_actionable_import_error():
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "crewai.events":
            raise ImportError("No module named 'crewai'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ImportError, match="crewai"):
            create_jiminy_event_listener(
                api_key="k",
                base_url="https://api.example.com",
                agent_owner="Acme",
                submitted_by="tenant-1",
            )
