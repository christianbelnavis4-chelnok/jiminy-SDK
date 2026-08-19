"""
Minimal quick-start script used to record the terminal demo GIF
(see assets/jiminy-quickstart.tape). Requires JIMINY_API_KEY and,
optionally, JIMINY_BASE_URL to be set in the environment.

    export JIMINY_API_KEY="your-api-key"
    python quickstart.py
"""

import os
import sys
from datetime import datetime, timezone

from jiminy_sdk import Client, TraceBuilder

API_KEY = os.environ.get("JIMINY_API_KEY")
BASE_URL = os.environ.get(
    "JIMINY_BASE_URL",
    "https://api.jiminy.uk",
)

if not API_KEY:
    print("Set JIMINY_API_KEY before running this script.", file=sys.stderr)
    sys.exit(1)

print("Building trace...")

builder = TraceBuilder(
    trace_id="quickstart-demo-001",
    agent_id="PA-Agent-01",
    agent_owner="Acme Insurance",
    submitted_by="Acme Compliance",
    task_description="Evaluate prior authorisation request",
    timestamp=datetime.now(tz=timezone.utc),
    domain_profile="health_insurance_prior_auth",
    hmac_key=os.environ.get("JIMINY_HMAC_KEY", "demo-hmac-key"),
)

builder.add_step(
    1,
    "eligibility_check",
    input={"member_id": "123"},
    output={"status": "eligible"},
    reasoning="Confirmed member active.",
)
builder.finalize("Approved. Auth reference: PA-2026-0417.")

trace = builder.build()

print("Submitting to Jiminy API...")

client = Client(api_key=API_KEY, base_url=BASE_URL)
result = client.evaluate(trace, mode="calibrate")

print(f"Verdict: {result['overall_verdict']}")
