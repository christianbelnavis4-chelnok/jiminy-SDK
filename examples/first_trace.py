"""
Jiminy — first trace example.

Submits a minimal calibration-mode trace to the Jiminy API and prints the
verdict and calibration report. Calibration mode does not persist the result
or fire any callbacks, so this is safe to run during setup and testing.

Usage:
    export JIMINY_API_KEY="your-api-key-here"
    export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
    python examples/first_trace.py

The script works without agent instrumentation — it sends a hand-crafted trace
that represents a typical single-step interaction. Replace the step content with
a real interaction once your agent is emitting traces.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

_BASE_URL = os.environ.get("JIMINY_BASE_URL", "").rstrip("/")
_API_KEY = os.environ.get("JIMINY_API_KEY", "")

if not _BASE_URL or not _API_KEY:
    print(
        "Set JIMINY_BASE_URL and JIMINY_API_KEY environment variables before running.\n"
        "Example:\n"
        "  export JIMINY_API_KEY='your-api-key-here'\n"
        "  export JIMINY_BASE_URL='https://jiminy-api-your-project.a.run.app'"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Build the trace
# ---------------------------------------------------------------------------
# Replace agent_owner with the exact name registered during onboarding.
# Replace submitted_by with your tenant ID (not the agent owner name).
# Replace the step content with a real agent interaction once ready.

AGENT_OWNER = os.environ.get("JIMINY_AGENT_OWNER", "Example-Agent")
TENANT_ID = os.environ.get("JIMINY_TENANT_ID", "example-tenant")

trace = {
    "trace_id": f"first-trace-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
    "agent_id": "example-agent-v1",
    "agent_owner": AGENT_OWNER,
    "submitted_by": TENANT_ID,
    "task_description": (
        "A customer asked whether their home insurance policy covers accidental damage "
        "to a laptop. The agent retrieved the policy terms and answered the question."
    ),
    "timestamp": datetime.now(UTC).isoformat(),
    "domain_profile": "general",
    "steps": [
        {
            "step_id": 1,
            "tool": "policy_lookup",
            "input": "policy_number=HOM-88421, query=accidental_damage_laptop",
            "output": (
                "Policy HOM-88421 includes accidental damage cover for personal "
                "electronics up to £1,500 per item, subject to £50 excess."
            ),
            "reasoning": (
                "Customer asked about laptop cover. Retrieved relevant clause from "
                "policy document. No personal data was accessed beyond"
                " the policy number "
                "provided by the customer."
            ),
        }
    ],
    "final_output": (
        "Yes, your policy covers accidental damage to laptops up to £1,500 per item "
        "with a £50 excess. To make a claim, please call 0800 XXX XXXX or visit "
        "our claims portal."
    ),
}

# ---------------------------------------------------------------------------
# Submit in calibration mode
# ---------------------------------------------------------------------------

print("Submitting calibration trace...")
print(f"  Agent owner : {AGENT_OWNER}")
print(f"  Trace ID    : {trace['trace_id']}")
print()

try:
    resp = requests.post(
        f"{_BASE_URL}/evaluate?mode=calibrate",
        json=trace,
        headers={
            "X-API-Key": _API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
except requests.exceptions.HTTPError as exc:
    print(f"HTTP error {exc.response.status_code}: {exc.response.text}")
    sys.exit(1)
except requests.exceptions.RequestException as exc:
    print(f"Request failed: {exc}")
    sys.exit(1)

result = resp.json()

# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

verdict = result.get("overall_verdict", "unknown")
confidence = result.get("confidence", "unknown")
integrity = result.get("trace_integrity", "unverified")
reliability = result.get("reliability") or {}
calibration = result.get("calibration_report") or {}

print(f"Verdict    : {verdict.upper()}")
print(f"Confidence : {confidence}")
print(f"Integrity  : {integrity}")

if reliability:
    print(
        f"Reliability: run_count={reliability.get('run_count', 1)}, "
        f"judge_model={reliability.get('judge_model', 'unknown')}"
    )

criteria_notes = calibration.get("criteria_notes") or []
if criteria_notes:
    print()
    print("Calibration report:")
    for note in criteria_notes:
        finding = note.get("finding", "?")
        criterion = note.get("criterion", "?")
        label = note.get("label", "?")
        icon = "✓" if finding == "PASS" else ("!" if finding == "CONCERN" else "✗")
        print(f"  {icon} {criterion} — {label}: {finding}")

suggestions = calibration.get("suggested_improvements") or []
if suggestions:
    print()
    print("Suggested improvements:")
    for s in suggestions:
        print(f"  • {s}")
else:
    print()
    print("Suggested improvements: none")

print()
print("Calibration complete. Results were not persisted (mode=calibrate).")
print("Run without ?mode=calibrate to submit a live evaluation.")
