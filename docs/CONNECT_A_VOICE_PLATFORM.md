# Connect a voice or conversational agent platform

Jiminy can score production call traffic from a voice or conversational
agent platform, not just traces submitted directly through the SDK. This
is delivered as an inbound webhook: the platform (or whoever operates the
integration on your behalf) posts each finished call to a Jiminy-provided
URL, and Jiminy converts it into an evaluation automatically.

This is available by request today, not yet fully self-serve — contact
your Jiminy contact to get a webhook URL and signing secret provisioned
for your account.

## How it works

1. Jiminy provisions a webhook URL and a shared signing secret for your
   account.
2. Your platform (or an integration you run) posts each call to that URL
   as JSON, signed over the raw request body.
3. Jiminy verifies the signature, converts the payload into a trace, and
   evaluates it the same way a trace submitted directly through the SDK
   would be — same auth model, same scoring, same read endpoints
   afterwards.

Evaluations arrive asynchronously: the webhook call itself is acknowledged
immediately, and the evaluation is available shortly after via the
ordinary evaluation history endpoints (see the [Quickstart](QUICKSTART.md)).

## Signing

Requests are signed with HMAC-SHA256 over the raw request body, using the
secret provisioned for your source. The signature is sent in an
`X-Signature` header as a lowercase hex digest:

```python
import hashlib
import hmac

signature = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
```

A request with a missing or invalid signature is rejected before any
processing happens.

## Payload shape

The default payload format is a turn-by-turn transcript. If your platform
doesn't expose discrete tool/function calls, this is enough on its own —
Jiminy synthesizes evaluable steps from the conversation itself, flagged
so the report clearly shows which steps came from an explicit tool call
versus which were reconstructed from the transcript.

```json
{
  "call_id": "unique-id-for-this-call",
  "agent_id": "your-agent-identifier",
  "timestamp": "2026-09-04T12:00:00Z",
  "turns": [
    { "speaker": "user", "text": "..." },
    { "speaker": "agent", "text": "..." }
  ]
}
```

If your platform's agents make explicit function/tool calls, those map
more directly to Jiminy's evaluation model and produce a higher-fidelity
result — this requires a small amount of platform-specific mapping, which
your Jiminy contact will set up with you as part of onboarding.

## Data handling

Call transcripts routinely contain your end customers' personal
information (names, account details, and similar). Before production call
data is sent to Jiminy, make sure a data processing agreement and a
retention policy are in place for your account — ask your Jiminy contact
if these aren't already set up.

## Independence

Jiminy does not support self-evaluation: the party submitting a trace for
evaluation must be independent of the party that owns the agent being
evaluated. In practice, for a webhook-connected platform, the submitting
party configured for your source should be *your organisation* (the
platform's customer), not the platform vendor itself.

## Further reading

- [Quickstart](QUICKSTART.md) — direct SDK submission, evaluation history,
  attestation
- [Attestation Spec](ATTESTATION_SPEC.md) — how trace integrity is verified
