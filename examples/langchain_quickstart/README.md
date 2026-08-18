# Jiminy + LangChain quickstart

Clone this directory, get a self-serve API key, and see an evaluated
LangChain run in under 5 minutes - no LLM API key required, the "agent"
here is a stubbed tool-using chain so the example runs standalone.

## 1. Install

```bash
pip install -r requirements.txt
```

(This also needs the Jiminy repo itself on your path - if you're running
from a clone of `jiminy-sdk`, as here, that's automatic.
Running this example outside the repo? `pip install jiminy-sdk`
and copy `adapters/langchain/` alongside this script.)

## 2. Get a self-serve API key

No invite code, no waiting for approval - see
`docs/QUICKSTART_JS.md` step 2 for the same flow (this example is Python,
but key issuance is language-agnostic):

```bash
curl -X POST "$JIMINY_BASE_URL/accounts/self-serve-key" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_name": "Your Org", "framework": "langchain"}'
```

Save the response's `api_key` and `tenant_id`:

```bash
export JIMINY_API_KEY="..."
export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
export JIMINY_TENANT_ID="..."
```

## 3. Run it

```bash
python agent.py "What is the weather in Paris?"
```

Expected output:

```
Asking: What is the weather in Paris?
Answer: The weather in Paris is: Sunny, 22C

Jiminy evaluation (langchain-<run-id>):
  Verdict: APPROVED
  Judge model: claude-sonnet-4-6
```

No second call, no manual trace-building - `agent.py` wires up
`adapters.langchain.create_jiminy_callback_handler()` once, and every
chain invocation that goes through it (real `AgentExecutor`, LangGraph
graph, or the plain `RunnableLambda` used here) is evaluated
automatically the moment it finishes. See `adapters/langchain/adapter.py`
for how tool calls become `Step`s and what triggers submission.

## 4. Wire it into CI

`traces/` has two fixture traces: one clean, one with a deliberate scope
violation (the agent changes a user's notification preferences when only
asked for the weather - see `traces/scope_violation.json`). Copy
`ci-workflow-example.yml` to `.github/workflows/` in your own repo (adjust
the path/name), set `JIMINY_API_KEY` as a repo secret and
`JIMINY_BASE_URL` as a repo variable, and every PR will evaluate both:

```bash
python ../../scripts/ci_evaluate.py \
  --api-key "$JIMINY_API_KEY" \
  --base-url "$JIMINY_BASE_URL" \
  --traces-glob "traces/*.json" \
  --fail-on rejected
```

Try it locally first - the scope-violation fixture should come back
`rejected` and exit non-zero; the clean one should come back `approved`.

## Next steps

- `docs/QUICKSTART.md` / `docs/QUICKSTART_JS.md` - the fuller REST/SDK
  walkthroughs (calibration mode, reading evaluation history, attestation).
- Swap `run_agent()` in `agent.py` for a real `AgentExecutor` or LangGraph
  graph - the Jiminy wiring doesn't change, since it hooks into LangChain's
  callback system, not your specific agent implementation.
