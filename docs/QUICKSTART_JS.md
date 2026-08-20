# Jiminy Quickstart — JavaScript/TypeScript

This is the JS-SDK mirror of `docs/QUICKSTART.md`, updated for the
self-serve signup path  — no invite code,
no manual review. If you're a design partner with an operator-issued key
instead, skip step 2 and use the key you were given.

---

## Prerequisites

- Node.js 18 or later
- A Firebase account (email/password or OAuth) — this is the one-time
  human signup step; everything after it is programmatic

---

## 1. Install the SDK

```bash
npm install "github:christianbelnavis4-chelnok/jiminy-sdk#path:clients/js"
```

Verify the install:

```bash
node -e "console.log(Object.keys(require('@ctbelnavis4/jiminy-sdk')))"
```

---

## 2. Get a self-serve API key

Sign up with Firebase Auth (any client SDK — this example assumes you
already have a Firebase ID token from your app's login flow), then call:

```bash
curl -X POST "$JIMINY_BASE_URL/accounts/self-serve-key" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_name": "Your Org", "framework": "langchain"}'
```

Response (shown once — there is no regenerate endpoint yet):

```json
{
  "tenant_id": "self-your-org-a1b2c3d4",
  "api_key": "...",
  "tier": "self_serve_free",
  "org_name": "Your Org",
  "note": "Store this key now — it is shown once and cannot be retrieved again."
}
```

Save both `tenant_id` and `api_key` — `tenant_id` is your `submitted_by`
value for every trace you submit.

```bash
export JIMINY_API_KEY="the api_key from the response"
export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
export JIMINY_TENANT_ID="the tenant_id from the response"
```

Self-serve tenants have an **open scope**: you may declare any
`agentOwner` name for your own traces — there's no operator pre-registering
owners per signup, unlike the invite-code design-partner path. The
self-serve free tier is capped at 25 evaluations/month and a 10 req/min
rate limit .

---

## 3. Build and submit your first trace

```js
const { Client, TraceBuilder } = require('@ctbelnavis4/jiminy-sdk');

const trace = new TraceBuilder({
  traceId: `first-trace-${Date.now()}`,
  agentId: 'my-agent-v1',
  agentOwner: 'My-Agent',                       // any name — open scope
  submittedBy: process.env.JIMINY_TENANT_ID,     // your tenant_id, not the agent owner
  taskDescription: 'Answered a customer query about their account balance.',
  timestamp: new Date(),
  domainProfile: 'general',
  hmacKey: process.env.JIMINY_HMAC_KEY || '',    // optional — see "Trace attestation" below
  framework: 'langchain',
})
  .addStep(1, 'account_lookup', {
    input: 'customer_id=12345',
    output: 'balance=£1,204.00, status=active',
    reasoning: 'Customer asked for current balance. Retrieved from account service.',
  })
  .finalize('Your current balance is £1,204.00 and your account is active.')
  .build();

const client = new Client({
  apiKey: process.env.JIMINY_API_KEY,
  baseUrl: process.env.JIMINY_BASE_URL,
});

client.evaluate(trace).then((result) => {
  console.log(`Verdict: ${result.overall_verdict}`);
  console.log(`Reliability: run_count=${result.reliability.run_count}`);
});
```

**You should see:**

```
Verdict: approved
Reliability: run_count=1
```

`overall_verdict` reflects what the judge actually found for your trace, so
a real run may print `flagged` or `rejected` instead — the shape of the
output (two lines, `overall_verdict` then `run_count`) is what confirms the
request worked.

Add `?mode=calibrate` first if you want a diagnostic, non-persisted run
before going live: `client.evaluate(trace, { mode: 'calibrate' })`.

---

## 4. Read your evaluation history

```bash
curl -s "$JIMINY_BASE_URL/evaluations" \
  -H "X-API-Key: $JIMINY_API_KEY"
```

Filters compose the same way as the Python/REST quickstart:
`?verdict=`, `?since=`, `?until=`, `?domain_profile=`, and more — see
`docs/QUICKSTART.md` step 5 for the full list, or `/docs` (Swagger UI).

---

## Trace attestation (tamper detection)

`TraceBuilder` signs each step with HMAC-SHA256 and includes a chain root
hash — the same scheme as the Python SDK, verified byte-for-byte against
the same golden vectors (`attestation_vectors/*.json`, see
`clients/js/test/traceBuilder.test.js`). Pass your per-tenant HMAC key as
`hmacKey`; without it, submissions evaluate with `trace_integrity:
unverified` rather than `verified` (never a hard failure). See
`docs/ATTESTATION_SPEC.md`.

---

## Troubleshooting

- **`403 Agent owner not in tenant scope`** — this shouldn't happen for a
  self-serve tenant (open scope by design). If you see it, check
  `submittedBy` matches your `tenant_id` exactly — that check still
  applies.
- **`409` on `/accounts/self-serve-key`** — a key was already issued for
  this Firebase account. Keys aren't retrievable after issuance; contact
  hello@jiminy.uk if the original was lost.
- **`429` on signup** — the signup endpoint is IP-throttled (5/hour) to
  prevent scripted mass account creation. Wait and retry.

## Further reading

- `docs/QUICKSTART.md` — the Python/REST version, includes calibration
  mode, multi-run reliability, and case study submission in more depth.
  scope, what "verified production traces" actually means for self-serve).
- `docs/ATTESTATION_SPEC.md` — the full canonicalisation and hash-chain spec.
