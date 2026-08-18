# Contributing to jiminy-sdk

This repo is the Apache 2.0-licensed evaluation SDK: client libraries,
framework adapters, and CI tooling for submitting traces to the hosted
Jiminy accountability API. The hosted platform, judge prompt library,
scoring rubric, and differential-treatment proxy set live outside this
repo and aren't covered here.

## Before you open an issue

Check [Discussions](../../discussions) first. Open questions ("how do I...",
"is X supported", "why does this behave this way") belong there, not in the
issue tracker. Issues are for concrete bugs and scoped feature requests.

## Dev setup

```bash
git clone https://github.com/christianbelnavis4-chelnok/jiminy-sdk.git
cd jiminy-sdk
pip install -e ".[dev,langchain,crewai]"
```

## Running tests

```bash
python -m pytest tests/ -q
```

CI also runs lint - worth running locally before opening a PR:

```bash
python -m ruff check .
```

## PR expectations

- Keep PRs scoped to one change. Unrelated cleanup makes review harder, not
  easier.
- Add or update tests for behaviour you change.
- Match existing code style - don't introduce new patterns for something
  the codebase already has a convention for.
- Describe *why* the change is needed in the PR description, not just what
  changed.
- Link the issue or discussion the PR addresses, if there is one.

## Reporting a security issue

Do not open a public issue for a security vulnerability. Contact
`hello@jiminy.uk` directly.
