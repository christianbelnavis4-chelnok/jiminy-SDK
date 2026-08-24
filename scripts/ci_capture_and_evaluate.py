#!/usr/bin/env python3
"""CI hook: run a LangChain agent inside the CI job and capture what it does
as DecisionTrace fixtures, instead of requiring a repo to hand-write or
pre-record traces/*.json up front.

This is the "auto-capture" path. The existing bring-your-own-traces path
(scripts/ci_evaluate.py, wired via .github/actions/evaluate/action.yml)
still applies afterward — this script only produces the trace files; it
never calls the Jiminy API itself. Wire both into one workflow as:

    - run: python scripts/ci_capture_and_evaluate.py --agent-module agent \
             --inputs inputs.json --traces-dir traces
    - uses: ./.github/actions/evaluate
      with:
        traces-glob: 'traces/*.json'

See examples/langchain_quickstart/ci-workflow-auto-capture.yml for a full
worked example.

Contract for the target agent module (imported via --agent-module):
  - It must expose a zero-argument callable (default name: build_chain)
    that returns a LangChain Runnable — anything with an .invoke(input,
    config) method, including a plain chain, an AgentExecutor, or a
    LangGraph graph compiled to a Runnable.
  - Each entry in --inputs (a JSON file: a list of either strings or
    {"input": ...} dicts) is passed to the chain as one invocation, wired
    through adapters.langchain.create_jiminy_callback_handler in
    capture-to-file mode.

Usage:
    python scripts/ci_capture_and_evaluate.py \
      --agent-module agent \
      --inputs inputs.json \
      --traces-dir traces \
      --agent-owner My-Agent \
      --submitted-by my-tenant-id
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys


def _load_inputs(path: str) -> list[dict]:
    with open(path) as f:
        raw = json.load(f)
    return [item if isinstance(item, dict) else {"input": item} for item in raw]


def run(
    *,
    agent_module: str,
    build_chain_attr: str,
    inputs_path: str,
    traces_dir: str,
    agent_owner: str,
    submitted_by: str,
    domain_profile: str,
    framework: str,
    trace_id_prefix: str,
) -> int:
    sys.path.insert(0, os.getcwd())
    module = importlib.import_module(agent_module)
    build_chain = getattr(module, build_chain_attr)
    chain = build_chain()

    sys.path.insert(
        0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from adapters.langchain import create_jiminy_callback_handler

    handler = create_jiminy_callback_handler(
        agent_owner=agent_owner,
        submitted_by=submitted_by,
        domain_profile=domain_profile,
        framework=framework,
        trace_id_prefix=trace_id_prefix,
        async_submit=False,
        capture_dir=traces_dir,
    )

    inputs = _load_inputs(inputs_path)
    if not inputs:
        print(f"::warning::No inputs found in {inputs_path}; nothing captured.")
        return 0

    for item in inputs:
        chain.invoke(item, config={"callbacks": [handler]})

    captured = sorted(os.listdir(traces_dir)) if os.path.isdir(traces_dir) else []
    print(f"Captured {len(captured)} trace(s) to {traces_dir}/")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--agent-module",
        required=True,
        help="Importable module exposing the chain factory (e.g. 'agent' for agent.py).",
    )
    p.add_argument(
        "--build-chain-attr",
        default="build_chain",
        help="Name of the zero-argument callable in --agent-module that returns "
        "a LangChain Runnable (default: build_chain).",
    )
    p.add_argument(
        "--inputs",
        required=True,
        help="Path to a JSON file: a list of input strings or {\"input\": ...} dicts, "
        "one per agent invocation to capture.",
    )
    p.add_argument(
        "--traces-dir",
        default="traces",
        help="Directory to write captured DecisionTrace JSON files into (default: traces).",
    )
    p.add_argument("--agent-owner", required=True)
    p.add_argument("--submitted-by", required=True)
    p.add_argument("--domain-profile", default="general")
    p.add_argument("--framework", default="langchain")
    p.add_argument("--trace-id-prefix", default="ci-capture")
    args = p.parse_args()

    sys.exit(
        run(
            agent_module=args.agent_module,
            build_chain_attr=args.build_chain_attr,
            inputs_path=args.inputs,
            traces_dir=args.traces_dir,
            agent_owner=args.agent_owner,
            submitted_by=args.submitted_by,
            domain_profile=args.domain_profile,
            framework=args.framework,
            trace_id_prefix=args.trace_id_prefix,
        )
    )


if __name__ == "__main__":
    main()
