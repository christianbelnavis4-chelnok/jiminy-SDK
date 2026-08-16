"""CrewAI event listener — automatic Jiminy evaluation on crew execution.

adapters/crewai/adapter.py converts an already-completed CrewAI run object
after the fact. This is different by design, same as
adapters/langchain/adapter.py: it hooks into CrewAI's global event bus
(crewai.events.crewai_event_bus) so an evaluation is submitted
automatically when a crew's kickoff completes — not as a manual extra
call.

Requires crewai (optional dependency — see pyproject.toml's ``crewai``
extra: ``pip install -e ".[crewai]"``). Only imported lazily inside
create_jiminy_event_listener(), so importing this module never requires
crewai to be installed.

Architectural note, unlike the LangChain adapter: CrewAI's event bus is a
process-global singleton, not passed per-invocation like a LangChain
``config={"callbacks": [...]}`` list. Registering a listener once (e.g.
at application startup) auto-evaluates every ``Crew.kickoff()`` call in
the process for its lifetime — there is no per-call opt-in, and no
built-in way to unregister short of process exit (CrewAI does expose
``crewai_event_bus.off()``/``scoped_handlers()`` for advanced use, e.g.
tests, but the common case is "call this once").

Per-kickoff state is correlated by the ``source`` object CrewAI passes to
every handler (the running ``Crew`` instance itself, keyed by ``id()`` —
stable for one kickoff's lifetime). Tool call start/end pairs are matched
FIFO per source, deliberately NOT via
``ToolUsageFinishedEvent.started_event_id`` — that field looks like a
caller-supplied correlation ID but isn't one: CrewAI's own
``_prepare_event`` overwrites it by popping a single **process-global,
LIFO** scope stack (``push_event_scope``/``pop_event_scope``, tracked via
a ContextVar), shared across every in-flight crew, not scoped per source.
Confirmed directly against a real crewai install: two crews with
interleaved tool calls (start A, start B, end A, end B) produced end A
paired with start B's ID, not its own — a real property of the library,
not a bug in this adapter's first draft, which is why this file doesn't
use it. A simple per-source FIFO queue avoids depending on that
internal, not-concurrency-safe mechanism entirely, and is correct for
the actual shape of tool execution within one crew (sequential, not
overlapping) even under multiple crews running at once.

Usage::

    from adapters.crewai.live import create_jiminy_event_listener

    create_jiminy_event_listener(
        api_key=os.environ["JIMINY_API_KEY"],
        base_url=os.environ["JIMINY_BASE_URL"],
        agent_owner="My-Crew",
        submitted_by=os.environ["JIMINY_TENANT_ID"],
        domain_profile="general",
        hmac_key=os.environ.get("JIMINY_HMAC_KEY"),
    )
    # Registered once — every crew.kickoff() call from here on is
    # evaluated automatically, nothing else to call.
    crew.kickoff(inputs={"topic": "..."})
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

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "clients", "python")
)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "raw"):
        # CrewOutput / TaskOutput — .raw is the plain-text final answer.
        return _stringify(value.raw)
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


@dataclass
class _RunState:
    task_description: str = ""
    steps: list[dict] = field(default_factory=list)
    error_events: list[str] = field(default_factory=list)
    final_output: str | None = None
    _pending_tools: list = field(default_factory=list)  # FIFO queue of partial steps
    _next_step_id: int = 1


def create_jiminy_event_listener(
    *,
    api_key: str,
    base_url: str,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str = "general",
    hmac_key: str | None = None,
    framework: str = "crewai",
    environment: str | None = None,
    trace_id_prefix: str = "crewai",
    async_submit: bool = True,
    on_result: Any = None,
    on_error: Any = None,
) -> None:
    """Register global CrewAI event listeners that auto-submit to Jiminy.

    Requires crewai to be importable — raises ImportError with an
    actionable message otherwise. Idempotent to call more than once is
    NOT guaranteed (each call registers a fresh set of handlers on the
    shared global bus) — call it once, typically at startup.

    async_submit=True (default) submits from a background thread so
    kickoff() isn't delayed by the evaluation round-trip; set False for
    synchronous submission.
    """
    try:
        from crewai.events import crewai_event_bus
        from crewai.events.types.crew_events import (
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
            CrewKickoffStartedEvent,
        )
        from crewai.events.types.tool_usage_events import (
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
            ToolUsageStartedEvent,
        )
    except ImportError as exc:
        raise ImportError(
            "adapters.crewai.live requires crewai. Install it with: "
            'pip install "jiminy-sdk[crewai]"  (or: pip install crewai)'
        ) from exc

    from jiminy_sdk import Client, JiminyAPIError, TraceBuilder

    client = Client(api_key=api_key, base_url=base_url)
    runs: dict[int, _RunState] = {}
    lock = threading.Lock()

    def _submit_now(trace_id: str, state: _RunState) -> None:
        try:
            if not state.steps:
                logger.debug(
                    "Jiminy: skipping submission for %s — no tool calls captured",
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
            logger.warning("Jiminy: unexpected error submitting %s: %s", trace_id, exc)
            if on_error is not None:
                on_error(trace_id, exc)

    def _submit(source: Any, state: _RunState) -> None:
        with lock:
            runs.pop(id(source), None)
        trace_id = f"{trace_id_prefix}-{getattr(source, 'id', id(source))}"
        if async_submit:
            threading.Thread(
                target=_submit_now, args=(trace_id, state), daemon=True
            ).start()
        else:
            _submit_now(trace_id, state)

    @crewai_event_bus.on(CrewKickoffStartedEvent)
    def _on_kickoff_start(source: Any, event: Any) -> None:
        with lock:
            runs[id(source)] = _RunState(task_description=_stringify(event.inputs))

    @crewai_event_bus.on(ToolUsageStartedEvent)
    def _on_tool_start(source: Any, event: Any) -> None:
        with lock:
            state = runs.setdefault(id(source), _RunState())
            state._pending_tools.append(
                {"tool": event.tool_name, "input": event.tool_args}
            )

    @crewai_event_bus.on(ToolUsageFinishedEvent)
    def _on_tool_end(source: Any, event: Any) -> None:
        with lock:
            state = runs.get(id(source))
            if state is None or not state._pending_tools:
                return
            pending = state._pending_tools.pop(0)
            step_id = state._next_step_id
            state._next_step_id += 1
            state.steps.append(
                {
                    "step_id": step_id,
                    "tool": pending["tool"],
                    "input": pending["input"],
                    "output": _stringify(event.output),
                    "reasoning": None,
                }
            )

    @crewai_event_bus.on(ToolUsageErrorEvent)
    def _on_tool_error(source: Any, event: Any) -> None:
        with lock:
            state = runs.get(id(source))
            if state is None:
                return
            if state._pending_tools:
                state._pending_tools.pop(0)
            state.error_events.append(f"tool_error[{event.tool_name}]: {event.error}")

    @crewai_event_bus.on(CrewKickoffCompletedEvent)
    def _on_kickoff_end(source: Any, event: Any) -> None:
        with lock:
            state = runs.get(id(source))
        if state is None:
            return
        state.final_output = _stringify(event.output)
        _submit(source, state)

    @crewai_event_bus.on(CrewKickoffFailedEvent)
    def _on_kickoff_failed(source: Any, event: Any) -> None:
        with lock:
            state = runs.get(id(source))
        if state is None:
            return
        state.error_events.append(f"kickoff_error: {event.error}")
        state.final_output = state.final_output or f"[kickoff error] {event.error}"
        _submit(source, state)
