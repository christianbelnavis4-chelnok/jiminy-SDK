# @jiminy/sdk

TypeScript/JavaScript SDK for the [Jiminy](https://jiminy.uk) AI agent
accountability API — build attested traces and submit them for evaluation.

Zero runtime dependencies (Node >=18, uses the built-in `fetch` and
`node:crypto`), mirroring the Python SDK (`clients/python/jiminy_sdk`) both
in shape and in this no-dependencies design.

## Install

Source-only for now —
install directly from the repo subdirectory:

```bash
npm install "github:christianbelnavis4-chelnok/jiminy-sdk#path:clients/js"
```

## Usage

```js
const { Client, TraceBuilder } = require('@jiminy/sdk');

const trace = new TraceBuilder({
  traceId: 'trace-001',
  agentId: 'my-agent-v1',
  agentOwner: 'Acme Corp',
  submittedBy: 'my-tenant-id',
  taskDescription: 'Answered a customer query about account balance.',
  timestamp: new Date(),
  domainProfile: 'general',
  hmacKey: process.env.JIMINY_HMAC_KEY,
  framework: 'langchain',
})
  .addStep(1, 'account_lookup', {
    input: { customerId: '12345' },
    output: { balance: '£1,204.00' },
    reasoning: 'Fetched balance from account service.',
  })
  .finalize('Your balance is £1,204.00.')
  .build();

const client = new Client({
  apiKey: process.env.JIMINY_API_KEY,
  baseUrl: process.env.JIMINY_BASE_URL,
});

const result = await client.evaluate(trace);
console.log(result.overall_verdict);
```

## Attestation

`TraceBuilder` signs each step with HMAC-SHA256, chained to the previous
step's hash, and includes a chain root hash — the same scheme as the Python
SDK and the server (`docs/ATTESTATION_SPEC.md`). The canonical JSON
serialisation (`src/canonical.js`) is verified byte-for-byte against the
same golden vectors (`attestation_vectors/*.json`) the Python SDK and server
are checked against — run `npm test` to see this checked directly,
including the Unicode vector (canonicalisation must escape non-ASCII
characters the same way Python's `json.dumps(..., ensure_ascii=True)`
does, which is not `JSON.stringify`'s default behaviour).

## Self-serve metadata

`environment` (`"test"` | `"production"`) and `framework` (free text, e.g.
`"langchain"`, `"crewai"`, `"otel"`) are optional `TraceBuilder` fields —
Neither is part of the HMAC payload.

## Testing

```bash
npm test
```

Runs Node's built-in test runner (`node --test`) against `test/` — no test
framework dependency.
