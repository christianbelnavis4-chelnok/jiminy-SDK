"""Tests for scripts/check_public_repo_hygiene.py — loaded via importlib since
scripts/ isn't a package, same approach as tests/test_ci_evaluate.py.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_public_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_public_repo_hygiene", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_public_repo_hygiene"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_clean_message_has_no_findings(mod):
    assert mod._scan_text("commit abc123", "Fix flaky retry logic in the client adapter") == []


def test_ticket_id_is_flagged(mod):
    findings = mod._scan_text("commit abc123", "Mirrors JIM-081 from the private repo")
    assert findings


def test_co_authored_by_claude_trailer_alone_is_flagged(mod):
    """Regression test: a commit carrying only a Co-Authored-By: Claude
    trailer, with no ticket ID, sprint name, repo name, or codename, must
    still fail the hygiene check (three merged commits on main slipped
    through before this pattern was added)."""
    message = (
        "Fix flaky retry logic in the client adapter\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>"
    )
    findings = mod._scan_text("commit abc123", message)
    assert findings
    assert any("Co-Authored-By" in f for f in findings)


def test_co_authored_by_claude_is_case_insensitive(mod):
    findings = mod._scan_text("commit abc123", "co-authored-by: claude <noreply@anthropic.com>")
    assert findings


def test_claude_session_line_alone_is_flagged(mod):
    message = "Fix flaky retry logic\n\nClaude-Session: https://claude.ai/code/session_abc123"
    findings = mod._scan_text("commit abc123", message)
    assert findings
    assert any("Claude-Session" in f or "session" in f.lower() for f in findings)


# --- Private terms read from the HYGIENE_BLOCKED_TERMS secret ----------------


def _terms(mod, monkeypatch, value):
    monkeypatch.setenv(mod.BLOCKED_TERMS_ENV, value)
    return mod._load_private_terms()


def test_private_term_is_flagged_case_insensitively(mod, monkeypatch):
    terms = _terms(mod, monkeypatch, "Example-Private-Repo\nExample-Codename")
    findings = mod._scan_text("commit abc123", "Sync from example-private-repo", terms)
    assert findings


def test_private_term_is_never_echoed(mod, monkeypatch):
    terms = _terms(mod, monkeypatch, "Example-Private-Repo")
    findings = mod._scan_text("commit abc123", "Sync from Example-Private-Repo", terms)
    assert findings
    assert all("example-private-repo" not in f.lower() for f in findings)


def test_private_term_must_match_whole_word(mod, monkeypatch):
    terms = _terms(mod, monkeypatch, "Example-Codename")
    assert mod._scan_text("commit abc123", "Example-Codenames are fine", terms) == []


def test_blank_lines_and_comments_are_ignored(mod, monkeypatch):
    terms = _terms(mod, monkeypatch, "# a comment\n\n   \nExample-Codename\n")
    assert len(terms) == 1


def test_no_private_terms_in_the_script_itself(mod, monkeypatch):
    """When the secret is available (CI on this repo), no private term may
    appear in the script. Skipped locally, where the terms are not known."""
    raw = os.environ.get(mod.BLOCKED_TERMS_ENV, "")
    terms = [t.strip().lower() for t in raw.splitlines() if t.strip() and not t.startswith("#")]
    if not terms:
        pytest.skip("HYGIENE_BLOCKED_TERMS not set")
    source = Path(mod.__file__).read_text(encoding="utf-8").lower()
    assert not [t for t in terms if t in source]


def _run_main(mod, monkeypatch):
    monkeypatch.setattr(mod, "_commit_range", lambda: "HEAD")
    monkeypatch.setattr(mod, "_run", lambda *a: "")
    monkeypatch.delenv("PR_TITLE", raising=False)
    monkeypatch.delenv("PR_BODY", raising=False)
    return mod.main()


def test_missing_secret_fails_closed(mod, monkeypatch, capsys):
    monkeypatch.delenv(mod.BLOCKED_TERMS_ENV, raising=False)
    monkeypatch.delenv("IS_FORK_PR", raising=False)
    assert _run_main(mod, monkeypatch) == 1
    assert "not set" in capsys.readouterr().out


def test_empty_secret_fails_closed(mod, monkeypatch):
    monkeypatch.setenv(mod.BLOCKED_TERMS_ENV, "  \n# only a comment\n")
    monkeypatch.delenv("IS_FORK_PR", raising=False)
    assert _run_main(mod, monkeypatch) == 1


def test_fork_pr_without_secret_warns_and_runs_builtin_checks(mod, monkeypatch, capsys):
    monkeypatch.delenv(mod.BLOCKED_TERMS_ENV, raising=False)
    monkeypatch.setenv("IS_FORK_PR", "true")
    assert _run_main(mod, monkeypatch) == 0
    assert "::warning::" in capsys.readouterr().out


def test_fork_pr_still_fails_on_builtin_patterns(mod, monkeypatch):
    monkeypatch.delenv(mod.BLOCKED_TERMS_ENV, raising=False)
    monkeypatch.setenv("IS_FORK_PR", "true")
    monkeypatch.setenv("PR_TITLE", "Mirror JIM-081")
    monkeypatch.setattr(mod, "_commit_range", lambda: "HEAD")
    monkeypatch.setattr(mod, "_run", lambda *a: "")
    assert mod.main() == 1


def test_secret_present_passes_clean_input(mod, monkeypatch):
    monkeypatch.setenv(mod.BLOCKED_TERMS_ENV, "Example-Private-Repo")
    assert _run_main(mod, monkeypatch) == 0
