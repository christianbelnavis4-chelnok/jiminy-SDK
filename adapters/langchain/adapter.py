"""LangChain callback handler — automatic Jiminy evaluation on agent execution.

Every other adapter in adapters/ (langsmith, crewai, otel, openai_agents)
converts an already-completed run object to a DecisionTrace after the fact
— a manual extra call the caller has to remember to make. This one is
different by design: it hooks into LangChain's live callback system so an
evaluation is submitted automatically when the top-level chain/agent
invocation finishes, rather than as a manual extra call.

Requires langchain-core (optional dependency — see pyproject.toml's
``langchain`` extra: ``pip install -e ".[langchain]"``). Only imported
lazily inside create_jiminy_callback_handler(), so importing this module
never requires langchain-core to be installed — matching every other
adapter's "no SDK installation required" posture for the parts of this
codebase that don't need it.

Usage::

    from adapters.langchain import create_jiminy_callback_handler

    handler = create_jiminy_callback_handler(
        api_key=os.environ["JIMINY_API_KEY"],
        base_url=os.environ["JIMINY_BASE_URL"],
        agent_owner="My-Agent",
        submitted_by=os.environ["JIMINY_TENANT_ID"],
        domain_profile="general",
        hmac_key=os.environ.get("JIMINY_HMAC_KEY"),  # optional — attestation
        framework="langchain",
    )
    agent_executor.invoke({"input": "..."}, config={"callbacks": [handler]})
    # Evaluation is submitted automatically once the top-level chain ends —
    # nothing else to call.

Scope note: captures tool calls (on_tool_start/on_tool_end) as Steps —
the same mapping adapters/langsmith uses for its "tool" run type. LLM
calls are not captured as separate steps by default (LangChain agents
typically make several LLM calls per tool call; including all of them
tends to produce noisy traces with no additional evaluable content beyond
what the tool calls already show). Pass ``capture_llm_calls=True`` to
include them if your agent's reasoning happens primarily in the LLM calls
themselves rather than via tools.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# jiminy_sdk is a separately-distributed sub-package (clients/python/jiminy_sdk,
# not a dependency of this main package — see clients/python/jiminy_sdk's own
# pyproject.toml), so it isn't necessarily importable directly. Same sys.path
# pattern tests/test_attestation.py already uses to reach it from within this
# repo. A real deployment installs jiminy_sdk separately (docs/QUICKSTART.md
# step 1) and this insert is then a harmless no-op.
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "clients", "python")
)


def _stringify(value: Any) -> str:
    """Best-effort readable string for a step input/output/task description."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


@dataclass
class _RunState:
    """Accumulated state for one top-level (root) chain invocation."""

    root_run_id: Any
    task_description: str = ""
    steps: list[dict] = field(default_factory=list)
    error_events: list[str] = field(default_factory=list)
    final_output: str | None = None
    # tool run_id -> partial step dict, moved into `steps` on on_tool_end.
    _pending_tools: dict = field(default_factory=dict)
    _next_step_id: int = 1


def create_jiminy_callback_handler(
    *,
    api_key: str,
    base_url: str,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    hmac_key: str | None = None,
    framework: str = "langchain",
    environment: str | None = None,
    trace_id_prefix: str = "langchain",
    capture_llm_calls: bool = False,
    async_submit: bool = True,
    on_result: Any = None,
    on_error: Any = None,
):
    """Build a LangChain BaseCallbackHandler that auto-submits to Jiminy.

    Requires langchain-core to be importable — raises ImportError with an
    actionable message otherwise, rather than failing on class definition
    at module import time (which would make this module unimportable for
    every other adapter/test that doesn't use LangChain at all).

    async_submit=True (default) submits from a background thread so the
    agent's own response isn't delayed by the evaluation round-trip;
    set False for synchronous submission (e.g. in a script where you want
    to wait for and inspect the result before the process exits).

    on_result(trace_id, result_dict) and on_error(trace_id, exception) are
    optional callables invoked after submission — useful for logging or
    surfacing verdicts in async_submit mode, where there's otherwise no
    return value to inspect.
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except ImportError as exc:
        raise ImportError(
            "adapters.langchain requires langchain-core. Install it with: "
            'pip install "jiminy-sdk[langchain]"  (or: pip install langchain-core)'
        ) from exc

    from jiminy_sdk import Client, JiminyAPIError, TraceBuilder

    client = Client(api_key=api_key, base_url=base_url)

    class JiminyCallbackHandler(BaseCallbackHandler):
        """Auto-submits one Jiminy evaluation per top-level chain invocation."""

        def __init__(self) -> None:
            super().__init__()
            self._runs: dict[Any, _RunState] = {}
            self._run_to_root: dict[Any, Any] = {}
            self._lock = threading.Lock()

        def _resolve_root(self, run_id: Any, parent_run_id: Any | None) -> Any:
            with self._lock:
                if parent_run_id is None:
                    self._run_to_root[run_id] = run_id
                    if run_id not in self._runs:
                        self._runs[run_id] = _RunState(root_run_id=run_id)
                    return run_id
                root = self._run_to_root.get(parent_run_id, parent_run_id)
                self._run_to_root[run_id] = root
                return root

        # -- chain lifecycle: root chain defines task_description/final_output --

        def on_chain_start(
            self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
        ) -> None:
            root = self._resolve_root(run_id, parent_run_id)
            if root == run_id:
                with self._lock:
                    state = self._runs[root]
                    if not state.task_description:
                        state.task_description = _stringify(inputs)[:2000]

        def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs) -> None:
            root = self._run_to_root.get(run_id, run_id)
            if root != run_id:
                return
            with self._lock:
                state = self._runs.get(root)
            if state is None:
                return
            state.final_output = _stringify(outputs)
            self._submit(state)

        def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
            root = self._run_to_root.get(run_id, run_id)
            with self._lock:
                state = self._runs.get(root)
                if state is not None:
                    state.error_events.append(f"chain_error: {error}")
            if root == run_id and state is not None:
                state.final_output = state.final_output or f"[chain error] {error}"
                self._submit(state)

        # -- tools: each on_tool_start/on_tool_end pair becomes one Step --

        def on_tool_start(
            self, serialized, input_str, *, run_id, parent_run_id=None,
            inputs=None, **kwargs
        ) -> None:
            root = self._resolve_root(run_id, parent_run_id)
            with self._lock:
                state = self._runs.setdefault(root, _RunState(root_run_id=root))
                tool_name = (serialized or {}).get("name", "unknown_tool")
                state._pending_tools[run_id] = {
                    "tool": tool_name,
                    "input": inputs if inputs is not None else input_str,
                }

        def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs) -> None:
            root = self._run_to_root.get(run_id, run_id)
            with self._lock:
                state = self._runs.get(root)
                if state is None:
                    return
                pending = state._pending_tools.pop(run_id, None)
                if pending is None:
                    return
                step_id = state._next_step_id
                state._next_step_id += 1
                state.steps.append(
                    {
                        "step_id": step_id,
                        "tool": pending["tool"],
                        "input": pending["input"],
                        "output": _stringify(output),
                        "reasoning": None,
                    }
                )

        def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
            root = self._run_to_root.get(run_id, run_id)
            with self._lock:
                state = self._runs.get(root)
                if state is None:
                    return
                pending = state._pending_tools.pop(run_id, None)
                tool_name = pending["tool"] if pending else "unknown_tool"
                state.error_events.append(f"tool_error[{tool_name}]: {error}")

        # -- optional: LLM calls as steps, off by default (see module docstring) --

        def on_llm_start(
            self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
        ) -> None:
            if not capture_llm_calls:
                return
            root = self._resolve_root(run_id, parent_run_id)
            with self._lock:
                state = self._runs.setdefault(root, _RunState(root_run_id=root))
                state._pending_tools[run_id] = {
                    "tool": "llm_call",
                    "input": prompts,
                }

        def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
            if not capture_llm_calls:
                return
            root = self._run_to_root.get(run_id, run_id)
            with self._lock:
                state = self._runs.get(root)
                if state is None:
                    return
                pending = state._pending_tools.pop(run_id, None)
                if pending is None:
                    return
                step_id = state._next_step_id
                state._next_step_id += 1
                try:
                    output = _stringify(
                        [g.text for gens in response.generations for g in gens]
                    )
                except Exception:  # noqa: BLE001
                    output = _stringify(response)
                state.steps.append(
                    {
                        "step_id": step_id,
                        "tool": pending["tool"],
                        "input": pending["input"],
                        "output": output,
                        "reasoning": None,
                    }
                )

        # -- submission --

        def _submit(self, state: _RunState) -> None:
            with self._lock:
                self._runs.pop(state.root_run_id, None)
            if async_submit:
                threading.Thread(
                    target=self._submit_now, args=(state,), daemon=True
                ).start()
            else:
                self._submit_now(state)

        def _submit_now(self, state: _RunState) -> None:
            trace_id = f"{trace_id_prefix}-{state.root_run_id}"
            try:
                if not state.steps:
                    logger.debug(
                        "Jiminy: skipping submission for %s — no tool calls captured "
                        "(set capture_llm_calls=True to evaluate LLM-only runs)",
                        trace_id,
                    )
                    return
                builder = TraceBuilder(
                    trace_id=trace_id,
                    agent_id=agent_owner,
                    agent_owner=agent_owner,
                    submitted_by=submitted_by,
                    task_description=state.task_description or "(no input captured)",
                    timestamp=datetime.now(UTC),
                    domain_profile=domain_profile,
                    hmac_key=hmac_key or "",
                    escalation_events=None,
                    error_events=state.error_events or None,
                    environment=environment,
                    framework=framework,
                )
                for step in state.steps:
                    builder.add_step(
                        step["step_id"],
                        step["tool"],
                        input=step["input"],
                        output=step["output"],
                        reasoning=step["reasoning"],
                    )
                builder.finalize(state.final_output or "(no output captured)")
                trace = builder.build()
                result = client.evaluate(trace)
                logger.info(
                    "Jiminy: evaluated %s -> %s", trace_id, result.get("overall_verdict")
                )
                if on_result is not None:
                    on_result(trace_id, result)
            except JiminyAPIError as exc:
                logger.warning("Jiminy: evaluation submission failed for %s: %s", trace_id, exc)
                if on_error is not None:
                    on_error(trace_id, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Jiminy: unexpected error submitting %s: %s", trace_id, exc
                )
                if on_error is not None:
                    on_error(trace_id, exc)

    return JiminyCallbackHandler()
