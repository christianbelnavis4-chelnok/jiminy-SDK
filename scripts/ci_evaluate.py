#!/usr/bin/env python3
"""CI hook: evaluate a set of DecisionTrace fixtures against Jiminy and fail
the build on a bad verdict.

Intended use: a repo that owns an agent checks in a handful of DecisionTrace
JSON fixtures (either hand-written or captured from real runs) representing
its agent's typical behaviour, and runs this script on every PR/commit —
the same idea as a snapshot/regression test, but the "expected" state is
"still passes independent evaluation" rather than a fixed golden output.
Wired up as a reusable composite action: .github/actions/evaluate/action.yml.

Usage:
    python scripts/ci_evaluate.py \
      --api-key "$JIMINY_API_KEY" \
      --base-url "$JIMINY_BASE_URL" \
      --traces-glob "traces/*.json" \
      --fail-on rejected

Exit code 0: every trace evaluated at or better than --fail-on.
Exit code 1: at least one trace's verdict met or exceeded --fail-on, or a
             trace failed to submit (network/API error) — a CI hook that
             silently skips failures is worse than one that's occasionally
             noisy about transient issues.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "clients", "python")
)

from jiminy_sdk import Client, JiminyAPIError  # noqa: E402

_VERDICT_SEVERITY = {"approved": 0, "flagged": 1, "rejected": 2}


def _load_traces(traces_glob: str) -> list[tuple[str, dict]]:
    paths = sorted(glob.glob(traces_glob))
    traces = []
    for path in paths:
        with open(path) as f:
            traces.append((path, json.load(f)))
    return traces


def run(
    *, api_key: str, base_url: str, traces_glob: str, fail_on: str, calibrate: bool
) -> tuple[int, list[dict]]:
    """Evaluate every matching trace. Returns (exit_code, rows) for reporting."""
    client = Client(api_key=api_key, base_url=base_url)
    traces = _load_traces(traces_glob)
    if not traces:
        print(f"::warning::No trace files matched glob: {traces_glob}")
        return 0, []

    threshold = _VERDICT_SEVERITY[fail_on]
    rows: list[dict] = []
    exit_code = 0

    for path, trace in traces:
        trace_id = trace.get("trace_id", path)
        try:
            result = client.evaluate(
                trace, mode="calibrate" if calibrate else "evaluate"
            )
        except JiminyAPIError as exc:
            print(f"::error::{path}: evaluation failed ({exc.status}): {exc.body}")
            rows.append(
                {"path": path, "trace_id": trace_id, "verdict": "ERROR", "error": str(exc)}
            )
            exit_code = 1
            continue

        verdict = result.get("overall_verdict", "unknown")
        failed_criteria = result.get("failed_criteria") or []
        rows.append(
            {
                "path": path,
                "trace_id": trace_id,
                "verdict": verdict,
                "failed_criteria": failed_criteria,
            }
        )
        severity = _VERDICT_SEVERITY.get(verdict, 2)
        if severity >= threshold:
            print(
                f"::error::{path} ({trace_id}): verdict={verdict}"
                + (f" failed_criteria={failed_criteria}" if failed_criteria else "")
            )
            exit_code = 1
        else:
            print(f"{path} ({trace_id}): verdict={verdict}")

    return exit_code, rows


def write_summary(rows: list[dict], summary_path: str | None) -> None:
    if not rows:
        return
    lines = ["| Trace | Verdict | Failed criteria |", "|---|---|---|"]
    for row in rows:
        verdict = row["verdict"]
        badge = {"approved": "✅", "flagged": "⚠️", "rejected": "❌", "ERROR": "🛑"}.get(
            verdict, "?"
        )
        criteria = ", ".join(row.get("failed_criteria") or []) or "—"
        lines.append(f"| `{row['trace_id']}` | {badge} {verdict} | {criteria} |")
    summary = "\n".join(lines) + "\n"

    target = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as f:
            f.write("## Jiminy evaluation results\n\n" + summary)
    else:
        print(summary)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-key", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument(
        "--traces-glob",
        default="traces/*.json",
        help="Glob pattern for DecisionTrace JSON fixtures (default: traces/*.json)",
    )
    p.add_argument(
        "--fail-on",
        choices=["flagged", "rejected"],
        default="rejected",
        help=(
            "Minimum verdict severity that fails the build. 'rejected' "
            "(default) only breaks CI on a hard rejection; 'flagged' also "
            "breaks CI on a flagged verdict."
        ),
    )
    p.add_argument(
        "--calibrate",
        action="store_true",
        help="Use ?mode=calibrate — doesn't persist or count against quota. "
        "Useful for a first CI integration before deciding on --fail-on.",
    )
    p.add_argument(
        "--summary-path",
        default=None,
        help="Write the results table here instead of $GITHUB_STEP_SUMMARY "
        "(mainly for local testing outside Actions).",
    )
    args = p.parse_args()

    exit_code, rows = run(
        api_key=args.api_key,
        base_url=args.base_url,
        traces_glob=args.traces_glob,
        fail_on=args.fail_on,
        calibrate=args.calibrate,
    )
    write_summary(rows, args.summary_path)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
