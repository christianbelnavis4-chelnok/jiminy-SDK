# Jiminy Quickstart

This guide takes you from zero to a submitted evaluation in under 10 minutes. Every command is copy-pasteable.

Two paths, pick one:

- **Self-serve** (most people) - no invite code, no human contact. Sign up with Firebase Auth and mint your own API key in step 2 below. Free tier: 25 evaluations/month, then paid credit plans - see [jiminy.uk/pricing](https://jiminy.uk/pricing). This is the default path in this guide.
- **Design partner** - an organisationally-verified account with an operator-issued API key, for teams wanting the independence guarantee enforced across separate organisations rather than self-declared. Apply via `POST /partner/onboard` (invite code required; email hello@jiminy.uk). If this is you, skip the self-serve part of step 2 and use the key your operator gave you, along with the tenant ID and agent owner name you were given at onboarding.

---

## Prerequisites

- Python 3.11 or later
- For self-serve: a Firebase account (email/password or OAuth) - this is the one-time human step; everything after it is programmatic
- For design partners: a Jiminy account provisioned via [app.jiminy.uk/signup](https://app.jiminy.uk/signup) and an operator-issued API key

---

## 1. Install the SDK

```bash
pip install jiminy-sdk
```

(Published on PyPI as of `jiminy-sdk` v0.1.0 - see
the SDK's PyPI package for
the full packaging/publish history. If you need an unreleased commit,
`pip install "git+https://github.com/christianbelnavis4-chelnok/jiminy-sdk.git#subdirectory=clients/python/jiminy_sdk"`
still works.)

Verify the install:

```bash
python -c "import jiminy_sdk; print('SDK ready')"
```

---

## 2. Get your credentials

**Self-serve:** sign up with Firebase Auth (any client SDK - or the Identity Toolkit REST API directly if you don't have a Firebase-enabled app already), then exchange the ID token for a Jiminy key:

```bash
curl -X POST "$JIMINY_BASE_URL/accounts/self-serve-key" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_name": "Your Org", "framework": "langchain"}'
```

Response (shown once - there is no regenerate endpoint yet, save both values):

```json
{
  "tenant_id": "self-your-org-a1b2c3d4",
  "api_key": "...",
  "tier": "self_serve_free",
  "org_name": "Your Org",
  "note": "Store this key now - it is shown once and cannot be retrieved again."
}
```

Self-serve accounts have an **open `agent_owner` scope** - no pre-registration needed, so `JIMINY_AGENT_OWNER` can be any name you choose for the agent you're evaluating (e.g. your own product's name).

**Either path**, set the environment variables the rest of this guide and `examples/first_trace.py` use:

```bash
export JIMINY_API_KEY="the api_key from above, or your operator-issued key"
export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
export JIMINY_AGENT_OWNER="Your-Agent-Name"   # self-serve: any name; design partner: the exact name you registered
export JIMINY_TENANT_ID="your-tenant-id"      # the tenant_id from the response above, or given at onboarding
```

Replace the URL with the Cloud Run endpoint (self-serve: the public one at the top of `README.md`; design partner: whatever your operator provided).
`JIMINY_AGENT_OWNER` and `JIMINY_TENANT_ID` are required by
`examples/first_trace.py` in step 3 below - without them it falls back to
placeholder values (`Example-Agent` / `example-tenant`) that are not in
your tenant's scope, and the request in step 3 will fail with
`403 Agent owner not in tenant scope` rather than demonstrate anything.

---

## 3. Run your first calibration trace

A calibration trace evaluates the judge against a sample interaction without persisting the result or counting against your quota. It is the right starting point - you see what the judge notices before going live.

```bash
python examples/first_trace.py
```

Expected output (exact formatting from `examples/first_trace.py` - the
specific verdict and findings below are illustrative; a real run reflects
what the judge actually finds for your trace):

```
Submitting calibration trace...
  Agent owner : ACME-Bot
  Trace ID    : first-trace-20260723T091500Z

Verdict    : APPROVED
Confidence : high
Integrity  : unverified
Reliability: run_count=1, judge_model=claude-sonnet-4-6

Calibration report:
  ✓ C1 - Scope Adherence: PASS
  ✓ C2 - Tool Authorisation: PASS
  ✓ C3 - Escalation Judgement: PASS
  ✓ C4 - Output Traceability: PASS
  ✓ C5 - Data Boundary: PASS

Suggested improvements: none

Calibration complete. Results were not persisted (mode=calibrate).
Run without ?mode=calibrate to submit a live evaluation.
```

`Integrity: unverified` is expected and correct here - `examples/first_trace.py`
sends a hand-crafted trace with no `step_hash`/`trace_root_hash`. See step 7
below to sign traces with `TraceBuilder` and get `trace_integrity: verified`.

---

## 4. Submit a live evaluation

Once you are satisfied with the calibration output, switch to live mode by removing `?mode=calibrate`. The result is persisted and included in your audit log.

```python
import os
import requests
from datetime import datetime, timezone

trace = {
    "trace_id": "my-first-live-trace-001",
    "agent_id": "acme-customer-service-v1",
    "agent_owner": "ACME-Bot",          # must match your registered agent owner
    "submitted_by": "your-tenant-id",   # your tenant ID, not the agent owner
    "task_description": "Answered a customer query about their account balance.",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "domain_profile": "general",
    "steps": [
        {
            "step_id": 1,
            "tool": "account_lookup",
            "input": "customer_id=12345",
            "output": "balance=£1,204.00, status=active",
            "reasoning": "Customer asked for current balance. Retrieved from account service."
        }
    ],
    "final_output": "Your current balance is £1,204.00 and your account is active."
}

resp = requests.post(
    f"{os.environ['JIMINY_BASE_URL']}/evaluate",
    json=trace,
    headers={"X-API-Key": os.environ["JIMINY_API_KEY"]},
)
resp.raise_for_status()
result = resp.json()
print(f"Verdict: {result['overall_verdict']}")
print(f"Reliability: run_count={result['reliability']['run_count']}")
```

**You should see:**

```
Verdict: approved
Reliability: run_count=1
```

`overall_verdict` reflects what the judge actually found for your trace, so a real run may print `flagged` or `rejected` instead - the shape of the output (two lines, `overall_verdict` then `run_count`) is what confirms the request worked.

---

## 5. Read your evaluation history

```bash
curl -s "$JIMINY_BASE_URL/evaluations" \
  -H "X-API-Key: $JIMINY_API_KEY" \
  | python3 -m json.tool | head -40
```

Filter by verdict:

```bash
curl -s "$JIMINY_BASE_URL/evaluations?verdict=rejected" \
  -H "X-API-Key: $JIMINY_API_KEY" \
  | python3 -m json.tool
```

**You should see:** a JSON array (empty `[]` if you have no rejected evaluations yet, or none from step 4 above), each entry containing the same `overall_verdict` / `reliability` / criteria-notes shape you saw in step 4's response.

---

## 6. Multi-run evaluations (higher reliability signal)

For borderline or high-stakes traces, run the judge multiple times and receive the conservative modal verdict:

```bash
curl -s -X POST "$JIMINY_BASE_URL/evaluate?runs=3" \
  -H "X-API-Key: $JIMINY_API_KEY" \
  -H "Content-Type: application/json" \
  -d @my_trace.json \
  | python3 -m json.tool
```

The `reliability.verdict_agreement` field tells you what fraction of runs agreed. If it is below 0.7, the verdict is contested - review the full criteria notes carefully.

**You should see:** `reliability.run_count` equal to `3` (matching `?runs=3`), and `reliability.verdict_agreement` as a number between 0 and 1. A stable, unambiguous trace typically agrees at `1.0` across all three runs.

---

## 7. Trace attestation (tamper detection)

**Design partners only** - this step needs a per-tenant HMAC signing key (`REDACTED_SECRET_NAME`), which is provisioned by your operator, not available to self-serve tenants. A self-serve trace without a signing key evaluates correctly but reports `trace_integrity: unverified`, not `broken` - that's expected, not an error.

To prove that submitted trace content has not been altered, use the `TraceBuilder` from the SDK. It signs each step with HMAC-SHA256 and includes a chain root hash in the submission.

```python
import os
from datetime import datetime, timezone
from jiminy_sdk import TraceBuilder

builder = TraceBuilder(
    trace_id="attested-trace-001",
    agent_id="acme-customer-service-v1",
    agent_owner="ACME-Bot",
    submitted_by="your-tenant-id",
    task_description="Answered a customer query about account balance.",
    timestamp=datetime.now(timezone.utc),
    domain_profile="general",
    hmac_key=os.environ["JIMINY_HMAC_KEY"],  # your per-tenant signing key
)

builder.add_step(
    1,
    "account_lookup",
    input="customer_id=12345",
    output="balance=£1,204.00",
    reasoning="Fetched balance from account service.",
)

builder.finalize("Your balance is £1,204.00.")
trace = builder.build()
```

**You should see:** `trace["trace_root_hash"]` set to a 64-character hex string, and `trace["steps"][0]["step_hash"]` likewise - both computed, neither `None`. Submitting this trace to `/evaluate` returns `trace_integrity: verified` rather than `unverified`.

The returned `trace` dict contains `step_hash` on each step and a `trace_root_hash` at the top level. Submissions with all hashes present return `trace_integrity: verified` in the verdict. See `docs/ATTESTATION_SPEC.md` for the full canonicalisation spec.

---

## 8. Submit a case study (after 10+ evaluations)

Once you have at least 10 evaluations, share your deployment experience via the case study endpoint. This data directly informs Jiminy's calibration and (with your consent) anonymised publication.

```python
import requests, os

study = {
    "agent_owner": "ACME-Bot",
    "domain_profile": "customer_service",
    "use_case_summary": "Live customer support for account balance enquiries.",
    "trace_count": 50,
    "verdict_distribution": {"approved": 44, "flagged": 5, "rejected": 1},
    "key_findings": [
        "Agent stayed within stated scope on all 50 traces.",
        "Human escalation path triggered correctly in 4 of 5 flagged cases.",
        "One rejection: agent disclosed account number without explicit consent prompt.",
    ],
    "service_tier": "internal_assurance",
    "published": False,
}

resp = requests.post(
    f"{os.environ['JIMINY_BASE_URL']}/partner/case-studies",
    json=study,
    headers={"X-API-Key": os.environ["JIMINY_API_KEY"]},
)
resp.raise_for_status()
print(resp.json()["case_study_id"])
```

**You should see:** a single printed string, the new case study's ID (e.g. `case-a1b2c3d4`). A `422` here instead usually means `trace_count` is below the 10-evaluation minimum - check `/evaluations` from step 5 to confirm you actually have enough.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `403 Agent owner not in tenant scope` | The `agent_owner` in the trace does not match your registered owners | Check your onboarding record and use the exact registered name |
| `403 submitted_by must match tenant identity` | The `submitted_by` field must equal your tenant ID, not the agent owner | Set `submitted_by` to your tenant ID |
| `429 Too Many Requests` | Rate limit hit | Reduce submission rate; the response's `Retry-After` header (seconds) tells you when to retry. Watch `X-RateLimit-Remaining` on your *successful* requests to see this coming before you hit it - it isn't present on the 429 itself |
| `trace_integrity: broken` | A step hash or root hash did not verify | Ensure the HMAC key matches `JIMINY_HMAC_KEY` and no fields were modified after signing |
| `trace_integrity: unverified` | No hashes present | Use `TraceBuilder` to sign traces before submission |

---

## Further reading

- `docs/ATTESTATION_SPEC.md` - canonicalisation spec and golden test vectors
- `examples/first_trace.py` - minimal runnable example (calibration mode)
- `clients/python/jiminy_sdk/` - full SDK source
- `POST /partner/onboard` - apply for design partner access
