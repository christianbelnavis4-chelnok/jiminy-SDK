"""Jiminy CrewAI quickstart — a minimal underwriting crew, evaluated
automatically via adapters.crewai.live.create_jiminy_event_listener.

Unlike the LangChain quickstart, this needs a real LLM (CrewAI's Agent
always plans through one — there's no zero-LLM RunnableLambda-style
escape hatch here). Set OPENAI_API_KEY (or configure a different LLM via
the `llm=` argument on Agent) before running.

Usage:
    pip install -r requirements.txt
    export OPENAI_API_KEY="..."
    export JIMINY_API_KEY="your key from POST /accounts/self-serve-key"
    export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
    export JIMINY_TENANT_ID="your tenant_id from the same response"
    python agent.py

See README.md for the self-serve signup step.
"""

from __future__ import annotations

import os
import sys

from crewai import Agent, Crew, Task
from crewai.tools import tool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from adapters.crewai.live import create_jiminy_event_listener  # noqa: E402


@tool("risk_data_lookup")
def risk_data_lookup(applicant_id: str) -> str:
    """Look up construction class, fire protection rating, and prior claims for an applicant."""
    # Stubbed for the quickstart — swap for a real risk-data API call.
    return (
        "construction_class=Standard non-combustible, sprinklered; "
        "fire_protection_rating=Grade 1; prior_claims_5yr=0"
    )


@tool("binding_authority_check")
def binding_authority_check(sum_insured: float) -> str:
    """Check whether a sum insured falls within delegated binding authority (limit: £1,000,000)."""
    limit = 1_000_000
    if sum_insured <= limit:
        return f"within_authority=true, limit={limit}"
    return f"within_authority=false, limit={limit}, excess={sum_insured - limit}"


def main() -> None:
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

    # Registered once — every crew.kickoff() call from here on is
    # evaluated automatically.
    create_jiminy_event_listener(
        api_key=api_key,
        base_url=base_url,
        agent_owner="Underwriting-Quickstart-Crew",
        submitted_by=tenant_id,
        domain_profile="insurance_underwriting",
        framework="crewai",
        environment="test",
        async_submit=False,  # print the verdict before the script exits
        on_result=lambda trace_id, result: results.append((trace_id, result)),
        on_error=lambda trace_id, exc: print(f"Jiminy submission failed: {exc}"),
    )

    underwriter = Agent(
        role="Commercial Property Underwriter",
        goal="Assess and price commercial property risk within delegated binding authority",
        backstory=(
            "An experienced underwriter who checks risk data and binding "
            "authority limits before pricing any commercial property risk."
        ),
        tools=[risk_data_lookup, binding_authority_check],
        verbose=False,
    )

    task = Task(
        description=(
            "Assess a commercial property application for Riverside Storage Ltd "
            "(applicant_id RIV-STOR-0192), sum insured £850,000. Look up the risk "
            "data, confirm the sum insured is within binding authority, and "
            "recommend whether to bind the policy."
        ),
        expected_output="A binding decision with the risk data and authority check cited.",
        agent=underwriter,
    )

    crew = Crew(agents=[underwriter], tasks=[task], verbose=False)
    output = crew.kickoff()
    print(f"Crew output: {output}")

    if results:
        trace_id, result = results[0]
        print()
        print(f"Jiminy evaluation ({trace_id}):")
        print(f"  Verdict: {result.get('overall_verdict', 'unknown').upper()}")
    else:
        print()
        print(
            "No Jiminy result captured — check JIMINY_BASE_URL/JIMINY_API_KEY, "
            "or that the crew actually called a tool (nothing to evaluate "
            "otherwise)."
        )


if __name__ == "__main__":
    main()
