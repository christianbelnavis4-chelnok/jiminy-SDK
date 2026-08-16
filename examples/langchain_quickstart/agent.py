"""Jiminy LangChain quickstart — a minimal tool-using chain, evaluated
automatically on every run via adapters.langchain.create_jiminy_callback_handler.

No agent framework boilerplate, no real LLM required to try this — the
"agent" here is a plain function wired up as a LangChain Runnable so the
example runs in seconds without an OpenAI/Anthropic API key. Swap
run_agent() for a real AgentExecutor/LangGraph graph once you're ready;
the Jiminy wiring (the callback handler) doesn't change.

Usage:
    pip install -r requirements.txt
    export JIMINY_API_KEY="your key from POST /accounts/self-serve-key"
    export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
    export JIMINY_TENANT_ID="your tenant_id from the same response"
    python agent.py "What is the weather in Paris?"

See README.md for the self-serve signup step and what to expect on screen.
"""

from __future__ import annotations

import os
import sys

from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from adapters.langchain import create_jiminy_callback_handler  # noqa: E402


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    # Stubbed for the quickstart — swap for a real API call.
    fake_forecasts = {
        "paris": "Sunny, 22C",
        "london": "Overcast, 15C",
        "tokyo": "Rainy, 19C",
    }
    return fake_forecasts.get(city.lower(), "Forecast unavailable")


def run_agent(inputs: dict) -> dict:
    """The 'agent' — extracts a city from the question, calls a tool, answers.

    Deliberately simple (no LLM call) so this file runs standalone. A real
    agent would call an LLM to decide which tool to use and how to phrase
    the answer; the Jiminy callback handler doesn't care how the chain
    reaches its tool calls, only that they go through LangChain's callback
    system, which any Runnable (including a real AgentExecutor) does.
    """
    question = inputs["input"]
    city = question.rstrip("?").split(" in ")[-1]
    weather = get_weather.invoke({"city": city})
    return {"output": f"The weather in {city} is: {weather}"}


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What is the weather in Paris?"

    api_key = os.environ.get("JIMINY_API_KEY")
    base_url = os.environ.get("JIMINY_BASE_URL")
    tenant_id = os.environ.get("JIMINY_TENANT_ID")
    if not api_key or not base_url or not tenant_id:
        print(
            "Set JIMINY_API_KEY, JIMINY_BASE_URL, and JIMINY_TENANT_ID first "
            "— see README.md."
        )
        sys.exit(1)

    results: list[tuple[str, dict]] = []

    handler = create_jiminy_callback_handler(
        api_key=api_key,
        base_url=base_url,
        agent_owner="Weather-Quickstart-Bot",
        submitted_by=tenant_id,
        domain_profile="general",
        framework="langchain",
        environment="test",
        async_submit=False,  # print the verdict before the script exits
        on_result=lambda trace_id, result: results.append((trace_id, result)),
        on_error=lambda trace_id, exc: print(f"Jiminy submission failed: {exc}"),
    )

    chain = RunnableLambda(run_agent)
    print(f"Asking: {question}")
    output = chain.invoke({"input": question}, config={"callbacks": [handler]})
    print(f"Answer: {output['output']}")

    if results:
        trace_id, result = results[0]
        print()
        print(f"Jiminy evaluation ({trace_id}):")
        print(f"  Verdict: {result.get('overall_verdict', 'unknown').upper()}")
        reliability = result.get("reliability") or {}
        if reliability:
            print(f"  Judge model: {reliability.get('judge_model', 'unknown')}")
    else:
        print()
        print(
            "No Jiminy result captured — this can happen if the chain didn't "
            "call any tools (nothing to evaluate) or the submission is still "
            "in flight. Check the JIMINY_BASE_URL/JIMINY_API_KEY are correct."
        )


if __name__ == "__main__":
    main()
