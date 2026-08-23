# Repository instructions

**This repository is public.** Nothing internal belongs in it — not in
file content, not in commit messages, not in PR titles/descriptions, not
in code comments. Treat every push here as permanent and world-readable
from the moment it lands, because it is: force-pushing to rewrite history
afterward is a last resort, not a safety net, and does not undo anything
for anyone who already cloned, forked, or cached it.

## Never include, anywhere in this repo or anything pushed to it

- Internal ticket IDs (`JIM-076`, `JIM-081`, etc.)
- Internal sprint/project names (`Sprint 3`, "Tier Decisions sprint", etc.)
- The private repo's name, in any casing (`JIMINY-dev`, `Jiminy-dev`)
- Internal feature codenames (e.g. `Verdict-to-Fixture`)
- `Claude-Session:` links or any other private session/tooling URL
- A `Co-Authored-By: Claude` trailer
- Internal business/ops detail, pricing, regulatory strategy, or anything
  from the private repo's `docs/` beyond what `scripts/github_triage.py`'s
  own docstring already explains is safe to read

If work here needs to reference or mirror something from the private
repo, describe *what changed and why* in plain terms a public reader
could see, not the internal name for it. "Mirrors an internal script
change" is fine; "Mirrors JIM-081 from JIMINY-dev" is not.

## Enforcement

`scripts/check_public_repo_hygiene.py` runs in CI (`.github/workflows/ci.yml`,
`hygiene` job) on every push and PR, scanning commit messages and PR
title/body against a pattern list covering the categories above. It fails
the build red if anything matches. This exists because a documentation-only
version of this policy existed in this exact file, twice, and was silently
deleted both times with no explanation — see git history on this file. Do
not weaken, remove, or bypass the CI check without discussing it with the
user first; if you believe a pattern is producing false positives, fix the
pattern, don't delete the check.

## What actually happened (23 Aug 2026)

Commit messages in this repo carried internal ticket IDs, a sprint name,
the private repo's name (both casings), a feature codename, and three
literal `Claude-Session` URLs — some going back to earlier sessions, not
just the one that found them. Required a full `git filter-repo` history
rewrite across all 10 branches and 2 tags, force-pushed to overwrite the
public history. That is not a repeatable fix — it only cleans up what's
already there, it doesn't prevent recurrence, and it can't reach anyone
who already had a clone or fork.

## Commit messages

Do not append a `Co-Authored-By: Claude` trailer or a `Claude-Session:`
link to commits in this repository.
