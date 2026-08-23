#!/usr/bin/env python3
"""Fail CI if any commit message, PR title, or PR body in this push/PR
contains an internal-only reference that has no business being in this
public repository's permanent history.

Exists because of a real incident (23 Aug 2026): commit messages here
carried internal ticket IDs, sprint names, the private JIMINY-dev repo
name, an internal feature codename, and literal Claude-Session URLs --
all had to be scrubbed via a full git-filter-repo history rewrite across
every branch and tag. This check exists so the next occurrence is a red
CI run before merge, not a post-hoc history rewrite after it's already
public. A prior, weaker version of this policy (CLAUDE.md noting not to
add Claude-Session/Co-Authored-By trailers) existed twice in this repo's
history and was silently deleted both times with no explanation --
documentation alone was not durable. This is deliberately an enforced
CI gate instead.

Checks (in order): every commit message in the push/PR range, plus the
PR title and body when running on a pull_request event. Never touches
file contents -- this is commit-metadata hygiene only, a different
concern from secret-scanning tools that check diffs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# Each entry: (compiled pattern, human-readable description). Patterns are
# deliberately specific enough to avoid false-positiving on this repo's own
# legitimate content (e.g. "jiminy-sdk" the package name, "Jiminy" the
# product name) -- see the docstring above for the incident that produced
# this exact list.
FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bJIM-\d+\b"), "internal ticket ID (JIM-NNN)"),
    (re.compile(r"\bSprint\s+\d+\b", re.IGNORECASE), "internal sprint name"),
    (re.compile(r"\bJiminy-dev\b", re.IGNORECASE), "internal repo name (JIMINY-dev)"),
    (re.compile(r"Claude-Session:\s*\S+"), "private Claude session URL"),
    (re.compile(r"\bVerdict-to-Fixture\b", re.IGNORECASE), "internal feature codename"),
]


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _commit_range() -> str:
    """Return a git rev-range covering every commit this push/PR adds."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request":
        base = os.environ["PR_BASE_SHA"]
        head = os.environ["PR_HEAD_SHA"]
        return f"{base}..{head}"

    before = os.environ.get("GITHUB_EVENT_BEFORE", "")
    after = os.environ.get("GITHUB_SHA", "HEAD")
    # A brand-new branch's first push has before=0000...0 -- nothing to
    # diff against, so just check the one commit that was pushed (falling
    # back further to just that single commit if it also has no parent,
    # e.g. a repo's very first commit).
    if not before or set(before) == {"0"}:
        try:
            _run("git", "rev-parse", f"{after}^")
        except subprocess.CalledProcessError:
            return after
        return f"{after}^..{after}"
    return f"{before}..{after}"


def _scan_text(label: str, text: str) -> list[str]:
    findings = []
    for pattern, description in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{label}: {description} -- matched {match.group()!r}")
    return findings


def main() -> int:
    findings: list[str] = []

    commit_range = _commit_range()
    log_output = _run("git", "log", "--format=%H%x00%s%x00%b%x03", commit_range)
    for record in log_output.split("\x03"):
        record = record.strip()
        if not record:
            continue
        sha, subject, body = record.split("\x00", 2)
        message = f"{subject}\n{body}"
        findings.extend(_scan_text(f"commit {sha[:8]}", message))

    pr_title = os.environ.get("PR_TITLE", "")
    pr_body = os.environ.get("PR_BODY", "")
    if pr_title:
        findings.extend(_scan_text("PR title", pr_title))
    if pr_body:
        findings.extend(_scan_text("PR description", pr_body))

    if findings:
        print("::error::Public-repo hygiene check failed -- internal references found:")
        for finding in findings:
            print(f"  - {finding}")
        print()
        print(
            "This repo is public. Internal ticket IDs, sprint names, the "
            "private repo name, feature codenames, and Claude session links "
            "must never appear in commit messages or PR text here. Rewrite "
            "the offending commit message(s) / PR description and push "
            "again. See scripts/check_public_repo_hygiene.py for the full "
            "incident history and pattern list."
        )
        return 1

    print("Public-repo hygiene check passed -- no internal references found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
