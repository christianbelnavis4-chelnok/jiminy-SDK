# jiminy-sdk

Attested trace builder and calibration tools for the [Jiminy](https://jiminy.uk)
AI agent evaluation API. Zero runtime dependencies — built on the Python
standard library only.

```bash
pip install jiminy-sdk
```

## What's in this package

- **`TraceBuilder`** — construct attested `DecisionTrace` payloads. Each
  step is signed with HMAC-SHA256, chained to the previous step's hash, so
  the server can cryptographically confirm the trace wasn't modified
  between emission and evaluation.
- **`CalibrationSession`** — wraps `TraceBuilder` to build and submit a
  trace in calibration mode: a diagnostic run that isn't persisted and
  doesn't count against quota, returning a per-criterion report to guide
  integration before going live.
- **`Client`** — a minimal synchronous wrapper around `POST /evaluate`.
  For anything beyond simple `evaluate()` calls (custom retries, async,
  connection pooling), bring your own HTTP client — the API is plain
  JSON over HTTPS.

## Usage

```python
from datetime import datetime, timezone
from jiminy_sdk import Client, TraceBuilder

builder = TraceBuilder(
    trace_id="...",
    agent_id="PA-Agent-01",
    agent_owner="Acme Insurance",
    submitted_by="Acme Compliance",
    task_description="Evaluate prior authorisation for MRI scan.",
    timestamp=datetime.now(tz=timezone.utc),
    domain_profile="health_insurance_prior_auth",
    hmac_key="your-per-tenant-hmac-key",
)

builder.add_step(
    1, "eligibility_check",
    input={"member_id": "123"}, output={"status": "Active"},
    reasoning="Confirmed member active.",
)
builder.finalize("Approved. Auth reference: PA-2026-001.")

trace = builder.build()  # a plain dict, ready to POST

client = Client(api_key="your-api-key", base_url="https://api.jiminy.uk")
result = client.evaluate(trace)
print(result["overall_verdict"])
```

The HMAC key must match the value configured as `SECRET_HMAC_KEYS` on the
server for your tenant. Traces built without `TraceBuilder` (or with the
wrong key) still evaluate — just with `trace_integrity` set to `"broken"`
or `"unverified"` instead of `"verified"`.

## Full documentation

See the [Quickstart guide](https://github.com/christianbelnavis4-chelnok/jiminy-sdk/blob/main/docs/QUICKSTART.md)
in this repository.

## License

Apache License 2.0 — see [LICENSE](LICENSE). This license covers this SDK
package only, not the wider Jiminy API/product codebase.
