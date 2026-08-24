#!/usr/bin/env python3
"""CI hook: evaluate a set of DecisionTrace fixtures against Jiminy and fail
the build on a bad verdict (docs/SELF_SERVE_SDK_SPEC.md).

Intended use: a repo that owns an agent checks in a handful of DecisionTrace
JSON fixtures (either hand-written or captured from real runs) representing
its agent's typical behaviour, and runs this script on every PR/commit —
the same idea as a snapshot/regression test, but the "expected" state is
"still passes independent evaluation" rather than a fixed golden output.
Wired up as a reusable composite action: see the christianbelnavis4-chelnok/jiminy-action repo.

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
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "clients", "python")
)

from jiminy_sdk import Client, JiminyAPIError  # noqa: E402

_VERDICT_SEVERITY = {"approved": 0, "flagged": 1, "rejected": 2}
_VERDICT_BADGES = {"approved": "✅", "flagged": "⚠️", "rejected": "❌", "ERROR": "🛑"}

# Zero-width marker hidden in the comment body so a later run can find and
# update its own previous comment instead of piling up a new one on every
# push to the same PR.
_PR_COMMENT_MARKER = "<!-- jiminy-ci-evaluate -->"


def _load_traces(traces_glob: str) -> list[tuple[str, dict]]:
    paths = sorted(glob.glob(traces_glob))
    traces = []
    for path in paths:
        with open(path) as f:
            traces.append((path, json.load(f)))
    return traces


# --fail-on-regression is deliberately advisory only, mirroring
# api/self_serve.py's "deliberately advisory only... no code path blocks"
# posture for quota enforcement. A regressed
# fixture is reported (::warning:: + a table column) but never flips
# exit_code — only --fail-on's existing verdict-severity check does that.
# This sprint ships detection of a fixture regression; hard-blocking merges
# on it is a deliberately separate, later decision.
def _load_active_fixtures(
    client: Client, agent_owners: list[str]
) -> list[tuple[str, dict]]:
    """Return [(agent_owner, fixture_dict), ...] for every active fixture
    across the given agent_owners. Errors for one owner are reported as a
    warning and skipped, not a build failure."""
    fixtures: list[tuple[str, dict]] = []
    for owner in agent_owners:
        try:
            for fixture in client.list_fixtures(owner, status="active"):
                fixtures.append((owner, fixture))
        except JiminyAPIError as exc:
            print(
                f"::warning::Could not load fixtures for agent_owner={owner} "
                f"({exc.status}): {exc.body}"
            )
    return fixtures


def _replay_fixtures_against_build(
    client: Client, fixtures: list[tuple[str, dict]]
) -> list[dict]:
    """Replay each fixture's synthetic_trace via ?mode=calibrate (no quota
    cost, no persistence — same call the main evaluate loop uses with
    --calibrate) and diff the result against the fixture's frozen
    expectation. Returns one row per fixture for the results table."""
    rows: list[dict] = []
    for owner, fixture in fixtures:
        scenario = fixture["scenario"]
        fixture_id = fixture["fixture_id"]
        try:
            response = client.evaluate(scenario["synthetic_trace"], mode="calibrate")
        except JiminyAPIError as exc:
            print(
                f"::warning::Fixture {fixture_id} ({owner}) failed to replay "
                f"({exc.status}): {exc.body}"
            )
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "agent_owner": owner,
                    "criterion": fixture.get("criterion", ""),
                    "regressed": None,
                }
            )
            continue

        actual_finding = None
        for note in (response.get("calibration_report") or {}).get(
            "criteria_notes", []
        ):
            if note.get("criterion") == scenario["expected_criterion"]:
                actual_finding = note.get("finding")
                break

        regressed = actual_finding != scenario["expected_finding"]
        rows.append(
            {
                "fixture_id": fixture_id,
                "agent_owner": owner,
                "criterion": fixture.get("criterion", ""),
                "regressed": regressed,
            }
        )
        if regressed:
            print(
                f"::warning::Fixture {fixture_id} ({owner}, {fixture.get('criterion', '')}) "
                f"regressed: expected {scenario['expected_finding']}, got {actual_finding!r}"
            )
        else:
            print(f"Fixture {fixture_id} ({owner}): still holds")

    return rows


def run(
    *,
    api_key: str,
    base_url: str,
    traces_glob: str,
    fail_on: str,
    calibrate: bool,
    fail_on_regression: bool = False,
) -> tuple[int, list[dict], list[dict], dict | None]:
    """Evaluate every matching trace, and optionally replay active fixtures.

    Returns (exit_code, rows, fixture_rows, ci_quota). fixture_rows is
    always [] when fail_on_regression is False. exit_code is never
    affected by fixture_rows — see the advisory-only note above
    _load_active_fixtures. ci_quota is None unless this run authenticated
    with a CI token: the API only sets
    X-CI-Quota-Used/X-CI-Quota-Limit response headers for CI-token
    traffic, so a regular API key's run has nothing to surface here.
    """
    client = Client(api_key=api_key, base_url=base_url)
    traces = _load_traces(traces_glob)
    if not traces:
        print(f"::warning::No trace files matched glob: {traces_glob}")
        return 0, [], [], None

    threshold = _VERDICT_SEVERITY[fail_on]
    rows: list[dict] = []
    exit_code = 0
    agent_owners: set[str] = set()
    ci_quota: dict | None = None

    for path, trace in traces:
        trace_id = trace.get("trace_id", path)
        agent_owner = trace.get("agent_owner")
        if agent_owner:
            agent_owners.add(agent_owner)
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
            if exc.status == 429:
                print(
                    "::error::CI credit allowance exhausted for this month "
                    "-- this is Jiminy's one hard-blocking quota. See "
                    "https://app.jiminy.uk/billing or contact hello@jiminy.uk."
                )
            continue

        headers = client.last_response_headers
        if "X-CI-Quota-Used" in headers:
            ci_quota = {
                "used": headers["X-CI-Quota-Used"],
                "limit": headers.get("X-CI-Quota-Limit", "?"),
            }

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

    if ci_quota:
        print(f"CI credit usage: {ci_quota['used']}/{ci_quota['limit']} this month")

    fixture_rows: list[dict] = []
    if fail_on_regression and agent_owners:
        fixtures = _load_active_fixtures(client, sorted(agent_owners))
        fixture_rows = _replay_fixtures_against_build(client, fixtures)

    return exit_code, rows, fixture_rows, ci_quota


def render_markdown_table(
    rows: list[dict],
    fixture_rows: list[dict] | None = None,
    ci_quota: dict | None = None,
) -> str:
    """Shared table renderer for the Step Summary and the PR comment, so the
    two surfaces never drift into showing different information.

    fixture_rows, when present, is appended as a second table rather than a
    column on the first — the two checks report on different things (a
    submitted trace's verdict vs. a stored fixture's regression status) and
    forcing them into one row-per-trace table would misrepresent fixtures
    that have no corresponding row in `rows` at all. ci_quota, when
    present, is a one-line note — this
    run authenticated with a CI token, so its hard-blocking credit usage
    is worth surfacing even though it isn't per-trace like the tables.
    """
    lines = ["| Trace | Verdict | Failed criteria |", "|---|---|---|"]
    for row in rows:
        verdict = row["verdict"]
        badge = _VERDICT_BADGES.get(verdict, "?")
        criteria = ", ".join(row.get("failed_criteria") or []) or "—"
        lines.append(f"| `{row['trace_id']}` | {badge} {verdict} | {criteria} |")
    table = "\n".join(lines) + "\n"

    if ci_quota:
        table += f"\nCI credit usage: **{ci_quota['used']}/{ci_quota['limit']}** this month.\n"

    if fixture_rows:
        fixture_lines = [
            "\n### Fixture regression check (advisory only)\n",
            "| Fixture | Agent | Criterion | Regressed vs frozen |",
            "|---|---|---|---|",
        ]
        for row in fixture_rows:
            regressed = row["regressed"]
            badge = "❓" if regressed is None else ("⚠️" if regressed else "✅")
            status = (
                "error" if regressed is None else ("yes" if regressed else "no")
            )
            fixture_lines.append(
                f"| `{row['fixture_id']}` | {row['agent_owner']} | "
                f"{row['criterion']} | {badge} {status} |"
            )
        table += "\n".join(fixture_lines) + "\n"

    return table


def write_summary(
    rows: list[dict],
    summary_path: str | None,
    fixture_rows: list[dict] | None = None,
    ci_quota: dict | None = None,
) -> None:
    if not rows:
        return
    summary = render_markdown_table(rows, fixture_rows, ci_quota)
    target = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as f:
            f.write("## Jiminy evaluation results\n\n" + summary)
    else:
        print(summary)


def _github_request(
    method: str, url: str, token: str, body: dict | None = None
) -> Any:
    """Thin wrapper around urllib for the GitHub REST API, isolated into its
    own function so tests can patch just the network boundary."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def find_pr_number(event_path: str | None = None) -> int | None:
    """Read the PR number from the GitHub Actions event payload.

    Returns None on any non-pull_request event (e.g. a push to main), or if
    no event payload is available (e.g. running this script locally) — not
    an error, just nothing to comment on.
    """
    event_path = event_path or os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        return None
    with open(event_path) as f:
        event = json.load(f)
    pr = event.get("pull_request")
    return pr.get("number") if pr else None


def post_or_update_pr_comment(
    rows: list[dict],
    *,
    github_token: str,
    repo: str,
    pr_number: int,
    fixture_rows: list[dict] | None = None,
    ci_quota: dict | None = None,
) -> None:
    """Post the results table as a PR comment, updating a previous run's own
    comment in place (matched via _PR_COMMENT_MARKER) rather than piling up
    a new comment on every push to the same PR.

    Never raises. A failed post — most commonly a PR from a fork, where
    GITHUB_TOKEN is always read-only regardless of the repo's own permission
    settings — is reported as a warning, not a build failure: the
    evaluation itself already ran and already gates the build via the
    script's exit code, so a comment that can't post shouldn't take the
    build down with it.
    """
    if not rows:
        return
    body = (
        f"{_PR_COMMENT_MARKER}\n## Jiminy evaluation results\n\n"
        f"{render_markdown_table(rows, fixture_rows, ci_quota)}"
    )
    api_base = f"https://api.github.com/repos/{repo}"
    try:
        comments = (
            _github_request(
                "GET", f"{api_base}/issues/{pr_number}/comments", github_token
            )
            or []
        )
        existing = next(
            (c for c in comments if _PR_COMMENT_MARKER in (c.get("body") or "")),
            None,
        )
        if existing:
            _github_request(
                "PATCH",
                f"{api_base}/issues/comments/{existing['id']}",
                github_token,
                {"body": body},
            )
        else:
            _github_request(
                "POST",
                f"{api_base}/issues/{pr_number}/comments",
                github_token,
                {"body": body},
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(
            f"::warning::Could not post PR comment (HTTP {exc.code}): {detail} "
            "-- this is often expected for PRs from forks, where GITHUB_TOKEN "
            "is always read-only regardless of repo permission settings. The "
            "evaluation itself still ran and gates the build normally."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Could not post PR comment: {exc}")


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
        "--fail-on-regression",
        action="store_true",
        help="Also replay every active fixture for each "
        "agent_owner referenced by --traces-glob and report any that no "
        "longer reproduce their frozen violation. Advisory only this "
        "sprint: never affects exit_code, only --fail-on does — reported "
        "as a ::warning:: and a second table, not a build failure.",
    )
    p.add_argument(
        "--summary-path",
        default=None,
        help="Write the results table here instead of $GITHUB_STEP_SUMMARY "
        "(mainly for local testing outside Actions).",
    )
    p.add_argument(
        "--comment-on-pr",
        action="store_true",
        help="Post (and keep updated) a PR comment with the results table. "
        "No-op on a non-pull_request event. Requires --github-token.",
    )
    p.add_argument(
        "--github-token",
        default=None,
        help="Token used to post the PR comment (only read if --comment-on-pr "
        "is set). Typically the workflow's own GITHUB_TOKEN.",
    )
    args = p.parse_args()

    exit_code, rows, fixture_rows, ci_quota = run(
        api_key=args.api_key,
        base_url=args.base_url,
        traces_glob=args.traces_glob,
        fail_on=args.fail_on,
        calibrate=args.calibrate,
        fail_on_regression=args.fail_on_regression,
    )
    write_summary(rows, args.summary_path, fixture_rows, ci_quota)

    if args.comment_on_pr:
        pr_number = find_pr_number()
        repo = os.environ.get("GITHUB_REPOSITORY")
        if pr_number is None:
            print(
                "::notice::--comment-on-pr set but this isn't a pull_request "
                "event; skipping PR comment."
            )
        elif not args.github_token or not repo:
            print(
                "::warning::--comment-on-pr set but --github-token or "
                "GITHUB_REPOSITORY is missing; skipping PR comment."
            )
        else:
            post_or_update_pr_comment(
                rows,
                github_token=args.github_token,
                repo=repo,
                pr_number=pr_number,
                fixture_rows=fixture_rows,
                ci_quota=ci_quota,
            )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
