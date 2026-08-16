# Jiminy + CrewAI quickstart

An underwriting crew (matching the `insurance_underwriting` domain
profile), evaluated automatically via
`adapters.crewai.live.create_jiminy_event_listener`.

Unlike the [LangChain quickstart](../langchain_quickstart/), this one
needs a real LLM — CrewAI's `Agent` always plans through one, there's no
zero-LLM escape hatch the way `RunnableLambda` gives LangChain. Budget a
couple of minutes and an `OPENAI_API_KEY` (or point `llm=` at a different
provider CrewAI supports).

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Get a self-serve API key

Same self-serve signup as every other quickstart — see
`docs/QUICKSTART_JS.md` step 2 (language-agnostic):

```bash
curl -X POST "$JIMINY_BASE_URL/accounts/self-serve-key" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_name": "Your Org", "framework": "crewai"}'
```

```bash
export OPENAI_API_KEY="..."
export JIMINY_API_KEY="the api_key from the response"
export JIMINY_BASE_URL="https://jiminy-api-<your-project>.a.run.app"
export JIMINY_TENANT_ID="the tenant_id from the response"
```

## 3. Run it

```bash
python agent.py
```

The crew assesses a commercial property application, checks binding
authority, and recommends a decision. Once `crew.kickoff()` returns, the
Jiminy evaluation has already been submitted and printed:

```
Crew output: ...
Jiminy evaluation (crewai-<crew-id>):
  Verdict: APPROVED
```

`create_jiminy_event_listener()` is registered once, globally — every
`Crew.kickoff()` call in the process from that point on is evaluated
automatically. See `adapters/crewai/live.py`'s module docstring for how
this differs architecturally from the LangChain adapter (CrewAI's event
bus is a process-global singleton, not passed per-invocation).

## 4. Wire it into CI

`traces/` has the same two insurance-underwriting fixtures used in the
main repo's demo set: `trace_05_underwriting_approval.json` (clean, within
binding authority) and `trace_06_underwriting_binding_authority_breach.json`
(a deliberate binding-authority breach — the agent binds a risk £3.2M
over its delegated limit without escalating). Copy
`ci-workflow-example.yml` to `.github/workflows/` in your own repo, same
as the LangChain quickstart's CI hook — see its README for the exact
secrets/variables to set.

## Next steps

- `adapters/crewai/adapter.py` (`from_crew_output`) — if you'd rather
  convert an already-completed `CrewOutput` manually instead of the live
  event listener here.
