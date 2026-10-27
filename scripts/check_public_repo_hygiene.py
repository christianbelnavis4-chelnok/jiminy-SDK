#!/usr/bin/env python3
"""Fail CI if any commit message, PR title, or PR body in this push/PR
contains an internal-only reference that has no business being in this
public repository's permanent history.

Exists because of a real incident (23 Aug 2026): commit messages here
carried internal ticket IDs, sprint names, the private repo's name, an
internal feature codename, and literal Claude-Session URLs --
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

Private terms (the private repo's name, internal codenames) are deliberately
NOT listed in this file, because a public file naming them would leak them.
They are read at run time from the HYGIENE_BLOCKED_TERMS repository secret:
one term per line, matched case-insensitively as whole words; blank lines and
lines starting with # are ignored. If the secret is missing or empty the check
FAILS rather than passing without it, except on pull requests from forks, which
GitHub never gives secrets to (a warning is printed and only the built-in
patterns run). Matched private terms are never echoed in the output.
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
    (re.compile(r"Claude-Session:\s*\S+", re.IGNORECASE), "private Claude session URL"),
    (re.compile(r"Co-Authored-By:\s*Claude\b", re.IGNORECASE), "Co-Authored-By: Claude trailer"),
]


BLOCKED_TERMS_ENV = "HYGIENE_BLOCKED_TERMS"


def _load_private_terms() -> list[re.Pattern]:
    """Compile the blocked terms from the HYGIENE_BLOCKED_TERMS secret."""
    raw = os.environ.get(BLOCKED_TERMS_ENV, "")
    terms = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE) for term in terms]


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


def _scan_text(
    label: str, text: str, private_terms: list[re.Pattern] | None = None
) -> list[str]:
    findings = []
    for pattern, description in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{label}: {description} -- matched {match.group()!r}")
    for pattern in private_terms or []:
        if pattern.search(text):
            # Never echo the term: this output lands in a public CI log.
            findings.append(f"{label}: a private term from {BLOCKED_TERMS_ENV} matched (value hidden)")
    return findings


def main() -> int:
    findings: list[str] = []

    private_terms = _load_private_terms()
    if not private_terms:
        if os.environ.get("IS_FORK_PR", "").lower() == "true":
            print(
                f"::warning::{BLOCKED_TERMS_ENV} is not available to pull requests from "
                "forks. Only the built-in patterns were checked; a maintainer should "
                "review the commit messages and PR text for private terms."
            )
        else:
            print(
                f"::error::{BLOCKED_TERMS_ENV} is not set, so private terms cannot be "
                "checked. Failing rather than passing without the check. A maintainer "
                "needs to add the repository secret (one term per line)."
            )
            return 1

    commit_range = _commit_range()
    log_output = _run("git", "log", "--format=%H%x00%s%x00%b%x03", commit_range)
    for record in log_output.split("\x03"):
        record = record.strip()
        if not record:
            continue
        sha, subject, body = record.split("\x00", 2)
        message = f"{subject}\n{body}"
        findings.extend(_scan_text(f"commit {sha[:8]}", message, private_terms))

    pr_title = os.environ.get("PR_TITLE", "")
    pr_body = os.environ.get("PR_BODY", "")
    if pr_title:
        findings.extend(_scan_text("PR title", pr_title, private_terms))
    if pr_body:
        findings.extend(_scan_text("PR description", pr_body, private_terms))

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
