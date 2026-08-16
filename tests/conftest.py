from __future__ import annotations

import pytest


@pytest.fixture
def sample_trace():
    return {
        "trace_id": "a1b2c3d4-0001-4e5f-8a9b-c0d1e2f3a4b5",
        "agent_id": "PA-Agent-07",
        "agent_owner": "NationalHealth Insurance Co.",
        "submitted_by": "State Insurance Commissioner Office",
        "task_description": "Prior auth test trace",
        "timestamp": "2026-06-20T09:14:32Z",
        "domain_profile": "health_insurance_prior_auth",
        "steps": [
            {
                "step_id": 1,
                "tool": "eligibility_check",
                "input": {"member_id": "TEST-001"},
                "output": {"status": "Active"},
                "reasoning": "Confirming eligibility.",
            }
        ],
        "final_output": "Approved",
        "escalation_events": [],
        "error_events": [],
    }
