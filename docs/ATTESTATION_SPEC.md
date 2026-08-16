# Jiminy Trace Attestation Specification

**Version:** 1.0  
**Status:** Normative  
**Implements:** SP3 PR 3.1

---

## Purpose

Trace attestation lets a verifier confirm that the evidence submitted to Jiminy has not been modified after emission. The SDK signs each step with a hash that chains to all previous steps; the trace root binds the full chain with a per-tenant HMAC key. The server recomputes the chain on receipt and records the integrity status (`verified`, `unverified`, or `broken`) on every verdict.

Attestation does not prove that evidence is complete. It proves that whatever was submitted has not been tampered with. The difference between "trust us" and "verify".

---

## Primitives

- **Step hashes:** HMAC-SHA256
- **Root hash:** HMAC-SHA256
- **Key:** per-tenant, hex-encoded, stored in GCP Secret Manager under `REDACTED_SECRET_NAME`
- **Encoding:** lowercase hexadecimal (64 characters per digest)

No external dependencies beyond the Python standard library `hmac` and `hashlib` modules.

---

## Canonicalisation

All hash inputs are deterministic JSON strings produced by this function:

```python
import json

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
```

Rules:
1. Object keys sorted lexicographically (ascending)
2. No whitespace: compact separators `(",", ":")`
3. Encoding: UTF-8
4. Non-JSON types (e.g. `datetime`) are converted to their `str()` representation via `default=str`
5. Nested objects and arrays are canonicalised recursively by the same `json.dumps` call
6. `null` values are preserved as JSON `null` (not omitted)

---

## Step Hash Construction

Each step carries a `step_hash` field. It is computed as:

```
step_hash[i] = HMAC-SHA256(key, canonical(step_payload[i]))
```

Where `step_payload[i]` is a dict with these exact keys (no others):

```python
{
    "step_id": step.step_id,          # int
    "tool":    step.tool,             # str
    "input":   canonical(step.input), # str — canonical JSON of the input value
    "output":  canonical(step.output),# str — canonical JSON of the output value
    "reasoning": step.reasoning or "",# str — empty string when reasoning is None
    "prev_hash": prev_hash,           # str — step_hash[i-1], or GENESIS_HASH for i=0
}
```

`GENESIS_HASH = "0" * 64` (64 zero characters).

The entire `step_payload` dict is itself canonicalised before being encoded to UTF-8 bytes for the HMAC call. This means the values of `input` and `output` inside the payload are the canonical JSON *strings* — i.e. double-canonicalised: first the value is serialised to a JSON string, then that string appears as a JSON string value inside the payload dict.

---

## Trace Root Hash

After all step hashes are computed, the trace root is:

```
trace_root_hash = HMAC-SHA256(key, canonical(root_payload))
```

Where `root_payload` is:

```python
{
    "trace_id":    trace.trace_id,    # str
    "agent_id":    trace.agent_id,    # str
    "step_hashes": step_hashes,       # list[str] — all step hashes in order
}
```

---

## Verification

The server re-derives all hashes from the submitted trace using the tenant's configured HMAC key. The result is one of:

| Status | Meaning |
|---|---|
| `verified` | All step hashes and root hash match the re-derived values |
| `unverified` | No `trace_root_hash` present (legacy/non-SDK submission), or the server has no key configured for this tenant |
| `broken` | Hashes present but at least one does not match; possible tampering |

Broken chains are **accepted and evaluated** but flagged prominently in the verdict. Rejection would incentivise strip-and-resubmit; flagging preserves the evidential record of the tamper.

---

## Python Reference Implementation

```python
import hashlib
import hmac
import json

GENESIS_HASH = "0" * 64

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def compute_step_hash(step_id, tool, inp, out, reasoning, prev_hash, key: bytes) -> str:
    payload = {
        "step_id": step_id,
        "tool": tool,
        "input": canonical(inp),
        "output": canonical(out),
        "reasoning": reasoning or "",
        "prev_hash": prev_hash,
    }
    return hmac.new(key, canonical(payload).encode(), hashlib.sha256).hexdigest()

def compute_root_hash(trace_id, agent_id, step_hashes: list, key: bytes) -> str:
    payload = {
        "trace_id": trace_id,
        "agent_id": agent_id,
        "step_hashes": step_hashes,
    }
    return hmac.new(key, canonical(payload).encode(), hashlib.sha256).hexdigest()
```

---

## Worked Example

**Input:**

```json
{
  "hmac_key": "test-key-vectors",
  "trace_id": "v01-0000-0000-0000",
  "agent_id": "test-agent-v01",
  "steps": [
    {
      "step_id": 1,
      "tool": "search",
      "input": "query",
      "output": "results",
      "reasoning": "Search for relevant data"
    }
  ]
}
```

**Step payload (before canonicalisation):**

```json
{
  "input": "\"query\"",
  "output": "\"results\"",
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "reasoning": "Search for relevant data",
  "step_id": 1,
  "tool": "search"
}
```

Note: `input` and `output` are canonical JSON strings of the original values. The string `"query"` serialises to the JSON string `"\"query\""`.

**Expected step hash:**

```
d4d6a8b9a6879dcf5c2154af9850b33968056a8e080f941b14d6ee0ad843808c
```

**Root payload (before canonicalisation):**

```json
{
  "agent_id": "test-agent-v01",
  "step_hashes": ["d4d6a8b9a6879dcf5c2154af9850b33968056a8e080f941b14d6ee0ad843808c"],
  "trace_id": "v01-0000-0000-0000"
}
```

**Expected root hash:**

```
434593367c637b0f30942bd45f15e64c4458620b177104638126a82792bb2c76
```

---

## Golden Test Vectors

Ten canonical test vectors are stored in `attestation_vectors/` at the repository root. Each file is a JSON object with these fields:

| Field | Type | Description |
|---|---|---|
| `vector_id` | str | Identifier (v01–v10) |
| `description` | str | What this vector tests |
| `hmac_key` | str | The signing key (test use only) |
| `trace` | object | Trace input (trace_id, agent_id, steps) |
| `expected_step_hashes` | list[str] | Expected hash for each step in order |
| `expected_root_hash` | str | Expected trace root hash |

| Vector | Tests |
|---|---|
| v01 | Single-step trace, string inputs |
| v02 | Three-step trace, hash chain |
| v03 | Unicode in step fields (French, German) |
| v04 | Null reasoning normalised to empty string |
| v05 | Structured dict input and output |
| v06 | Different HMAC key produces different hashes |
| v07 | Empty string input and output |
| v08 | List input and output values |
| v09 | Five-step trace (maximum realistic depth) |
| v10 | Step IDs starting from zero |

A conforming implementation must pass all 10 vectors (correct step hashes and root hash). A mutated trace (any field changed) must produce a different hash.

---

## SDK Notes

The Jiminy Python SDK (`jiminy_sdk.TraceBuilder`) computes and embeds all hashes automatically. Callers provide the HMAC key at construction time and never interact with hash primitives directly. See `clients/python/jiminy_sdk/builder.py` for the reference SDK implementation, which shares the canonicalisation logic with this spec.
