"""Tests for adapters/langchain/adapter.py — the automatic-evaluation
LangChain callback handler.

Unlike the other adapter tests (langsmith, crewai, otel, openai_agents),
which convert a completed run object, this exercises a *live* callback
handler by calling its methods directly with real UUIDs and the exact
keyword signatures langchain-core's BaseCallbackHandler defines — the
same approach LangChain's own test suite uses to test callback handlers
without running a full agent framework.

Requires langchain-core (pytest.importorskip — skipped, not failed, in
any environment where it isn't installed, since it's an optional
dependency of the main package, same as every other langchain-only file).
"""

from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

import pytest

langchain_core = pytest.importorskip("langchain_core")

from adapters.langchain import create_jiminy_callback_handler  # noqa: E402


def _handler(**overrides):
    defaults = dict(
        api_key="k",
        base_url="https://api.example.com",
        agent_owner="Acme",
        submitted_by="tenant-1",
        domain_profile="general",
        async_submit=False,
    )
    defaults.update(overrides)
    return create_jiminy_callback_handler(**defaults)


class _FakeEvaluate:
    """Records every trace submitted, standing in for Client.evaluate.

    Deliberately takes (self, trace) rather than (self, client_self,
    trace): an instance with __call__ isn't a descriptor, so patching
    Client.evaluate with one does not auto-bind the Client instance the
    way assigning a plain function would.
    """

    def __init__(self, verdict="approved"):
        self.calls: list[dict] = []
        self._verdict = verdict

    def __call__(self, trace, **kwargs):
        self.calls.append(trace)
        return {"overall_verdict": self._verdict}


class TestSingleToolCall:
    def test_submits_trace_with_one_step(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            tool = uuid.uuid4()

            handler.on_chain_start({}, {"input": "weather in Paris?"}, run_id=root, parent_run_id=None)
            handler.on_tool_start(
                {"name": "get_weather"}, "Paris", run_id=tool, parent_run_id=root,
                inputs={"city": "Paris"},
            )
            handler.on_tool_end("Sunny, 22C", run_id=tool, parent_run_id=root)
            handler.on_chain_end({"output": "Sunny and 22C."}, run_id=root, parent_run_id=None)

        assert len(fake.calls) == 1
        trace = fake.calls[0]
        assert trace["agent_owner"] == "Acme"
        assert trace["submitted_by"] == "tenant-1"
        assert trace["framework"] == "langchain"
        assert len(trace["steps"]) == 1
        assert trace["steps"][0]["tool"] == "get_weather"
        assert trace["steps"][0]["input"] == {"city": "Paris"}
        assert trace["steps"][0]["output"] == "Sunny, 22C"
        assert "trace_root_hash" in trace

    def test_no_tool_calls_skips_submission_by_default(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            handler.on_chain_start({}, {"input": "hello"}, run_id=root, parent_run_id=None)
            handler.on_chain_end({"output": "hi"}, run_id=root, parent_run_id=None)
        assert fake.calls == []


class TestMultipleToolCallsOrdering:
    def test_steps_are_ordered_and_incrementing(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            handler.on_chain_start({}, {"input": "multi-step"}, run_id=root, parent_run_id=None)
            for i, tid in enumerate([t1, t2, t3]):
                handler.on_tool_start(
                    {"name": f"tool_{i}"}, f"in_{i}", run_id=tid, parent_run_id=root
                )
                handler.on_tool_end(f"out_{i}", run_id=tid, parent_run_id=root)
            handler.on_chain_end({"output": "done"}, run_id=root, parent_run_id=None)

        steps = fake.calls[0]["steps"]
        assert [s["step_id"] for s in steps] == [1, 2, 3]
        assert [s["tool"] for s in steps] == ["tool_0", "tool_1", "tool_2"]


class TestNestedChains:
    def test_tool_call_under_nested_chain_attributes_to_root(self):
        """A tool called from within a sub-chain (parent_run_id chains two
        levels deep) must still be attributed to the top-level invocation,
        not treated as its own unrelated evaluation."""
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            sub_chain = uuid.uuid4()
            tool = uuid.uuid4()

            handler.on_chain_start({}, {"input": "nested"}, run_id=root, parent_run_id=None)
            handler.on_chain_start({}, {"input": "sub"}, run_id=sub_chain, parent_run_id=root)
            handler.on_tool_start(
                {"name": "nested_tool"}, "x", run_id=tool, parent_run_id=sub_chain
            )
            handler.on_tool_end("y", run_id=tool, parent_run_id=sub_chain)
            handler.on_chain_end({"output": "sub done"}, run_id=sub_chain, parent_run_id=root)
            handler.on_chain_end({"output": "root done"}, run_id=root, parent_run_id=None)

        # Only the root chain's on_chain_end triggers submission.
        assert len(fake.calls) == 1
        assert fake.calls[0]["steps"][0]["tool"] == "nested_tool"


class TestConcurrentRootRuns:
    def test_two_independent_invocations_do_not_mix_state(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root_a, root_b = uuid.uuid4(), uuid.uuid4()
            tool_a, tool_b = uuid.uuid4(), uuid.uuid4()

            handler.on_chain_start({}, {"input": "A"}, run_id=root_a, parent_run_id=None)
            handler.on_chain_start({}, {"input": "B"}, run_id=root_b, parent_run_id=None)
            handler.on_tool_start({"name": "tool_a"}, "a", run_id=tool_a, parent_run_id=root_a)
            handler.on_tool_start({"name": "tool_b"}, "b", run_id=tool_b, parent_run_id=root_b)
            handler.on_tool_end("out_a", run_id=tool_a, parent_run_id=root_a)
            handler.on_tool_end("out_b", run_id=tool_b, parent_run_id=root_b)
            handler.on_chain_end({"output": "done_a"}, run_id=root_a, parent_run_id=None)
            handler.on_chain_end({"output": "done_b"}, run_id=root_b, parent_run_id=None)

        assert len(fake.calls) == 2
        tools_by_call = [c["steps"][0]["tool"] for c in fake.calls]
        assert sorted(tools_by_call) == ["tool_a", "tool_b"]


class TestErrors:
    def test_tool_error_recorded_without_a_step(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            tool = uuid.uuid4()

            handler.on_chain_start({}, {"input": "will fail"}, run_id=root, parent_run_id=None)
            handler.on_tool_start({"name": "flaky_tool"}, "x", run_id=tool, parent_run_id=root)
            handler.on_tool_error(RuntimeError("boom"), run_id=tool, parent_run_id=root)
            # A second, successful tool call so the trace has >=1 step and submits.
            tool2 = uuid.uuid4()
            handler.on_tool_start({"name": "ok_tool"}, "y", run_id=tool2, parent_run_id=root)
            handler.on_tool_end("fine", run_id=tool2, parent_run_id=root)
            handler.on_chain_end({"output": "recovered"}, run_id=root, parent_run_id=None)

        trace = fake.calls[0]
        assert len(trace["steps"]) == 1
        assert trace["steps"][0]["tool"] == "ok_tool"
        assert any("flaky_tool" in e for e in trace["error_events"])

    def test_chain_error_still_submits_root_run(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            tool = uuid.uuid4()

            handler.on_chain_start({}, {"input": "will error"}, run_id=root, parent_run_id=None)
            handler.on_tool_start({"name": "t"}, "x", run_id=tool, parent_run_id=root)
            handler.on_tool_end("y", run_id=tool, parent_run_id=root)
            handler.on_chain_error(RuntimeError("chain blew up"), run_id=root, parent_run_id=None)

        assert len(fake.calls) == 1
        assert "chain blew up" in fake.calls[0]["error_events"][0]


class TestLLMCallCapture:
    def test_capture_llm_calls_off_by_default(self):
        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler()
            root = uuid.uuid4()
            llm = uuid.uuid4()

            handler.on_chain_start({}, {"input": "x"}, run_id=root, parent_run_id=None)
            handler.on_llm_start({}, ["prompt"], run_id=llm, parent_run_id=root)
            # No on_llm_end assertion needed — capture is off, nothing recorded.
            handler.on_chain_end({"output": "y"}, run_id=root, parent_run_id=None)

        assert fake.calls == []  # no tool steps, LLM capture off -> skipped

    def test_capture_llm_calls_enabled(self):
        from langchain_core.outputs import Generation, LLMResult

        fake = _FakeEvaluate()
        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler(capture_llm_calls=True)
            root = uuid.uuid4()
            llm = uuid.uuid4()

            handler.on_chain_start({}, {"input": "x"}, run_id=root, parent_run_id=None)
            handler.on_llm_start({}, ["prompt"], run_id=llm, parent_run_id=root)
            handler.on_llm_end(
                LLMResult(generations=[[Generation(text="the answer")]]),
                run_id=llm,
                parent_run_id=root,
            )
            handler.on_chain_end({"output": "y"}, run_id=root, parent_run_id=None)

        assert len(fake.calls) == 1
        assert fake.calls[0]["steps"][0]["tool"] == "llm_call"


class TestAsyncSubmit:
    def test_async_submit_runs_in_background_thread(self):
        fake = _FakeEvaluate()
        done = threading.Event()

        def on_result(trace_id, result):
            done.set()

        with patch("jiminy_sdk.client.Client.evaluate", fake):
            handler = _handler(async_submit=True, on_result=on_result)
            root = uuid.uuid4()
            tool = uuid.uuid4()
            handler.on_chain_start({}, {"input": "x"}, run_id=root, parent_run_id=None)
            handler.on_tool_start({"name": "t"}, "x", run_id=tool, parent_run_id=root)
            handler.on_tool_end("y", run_id=tool, parent_run_id=root)
            # on_chain_end must return immediately, before submission completes.
            handler.on_chain_end({"output": "z"}, run_id=root, parent_run_id=None)
            assert done.wait(timeout=2), "async submission did not complete in time"

        assert len(fake.calls) == 1


class TestErrorHandling:
    def test_evaluate_failure_does_not_raise(self):
        def raising_evaluate(self, trace, **kwargs):
            from jiminy_sdk import JiminyAPIError

            raise JiminyAPIError(403, {"detail": "Invalid API key."})

        errors = []
        with patch("jiminy_sdk.client.Client.evaluate", raising_evaluate):
            handler = _handler(on_error=lambda tid, exc: errors.append(exc))
            root = uuid.uuid4()
            tool = uuid.uuid4()
            handler.on_chain_start({}, {"input": "x"}, run_id=root, parent_run_id=None)
            handler.on_tool_start({"name": "t"}, "x", run_id=tool, parent_run_id=root)
            handler.on_tool_end("y", run_id=tool, parent_run_id=root)
            # Must not raise, even though evaluate() raises internally.
            handler.on_chain_end({"output": "z"}, run_id=root, parent_run_id=None)

        assert len(errors) == 1


def test_missing_langchain_core_raises_actionable_import_error():
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain_core.callbacks":
            raise ImportError("No module named 'langchain_core'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ImportError, match="langchain-core"):
            create_jiminy_callback_handler(
                api_key="k",
                base_url="https://api.example.com",
                agent_owner="Acme",
                submitted_by="tenant-1",
            )
