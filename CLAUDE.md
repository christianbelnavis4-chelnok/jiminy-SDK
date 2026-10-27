# Repository instructions

**This repository is public.** Treat every push as permanent and
world-readable from the moment it lands. Nothing internal belongs in it: not
in file content, not in code comments, not in commit messages, and not in PR
titles or descriptions.

## Never include

- Internal ticket IDs (for example `JIM-NNN`)
- Internal sprint or project names (for example `Sprint 3`)
- The name of any private repository, in any casing
- Internal feature codenames
- `Claude-Session:` links or any other private session or tooling URL
- A `Co-Authored-By: Claude` trailer
- Internal business or operations detail: pricing, regulatory strategy, judge
  engine internals, calibration methodology, dashboard or website source

When work here mirrors something from elsewhere, describe what changed and
why in plain terms a public reader could follow, not by its internal name.

## Enforced check

`scripts/check_public_repo_hygiene.py` runs in CI and fails on forbidden
references in commit messages, PR titles and PR bodies. It exists because
internal references once had to be scrubbed from this repository's history,
and documentation alone was not durable.

- Do not remove, weaken or bypass the check, and do not delete or shrink this
  file, without confirming with the maintainer first.
- Run the check locally before pushing.

## Commit messages

Use plain, descriptive messages. Do not append a `Co-Authored-By: Claude`
trailer or a `Claude-Session:` link.
