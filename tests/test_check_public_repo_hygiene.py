"""Tests for scripts/check_public_repo_hygiene.py — loaded via importlib since
scripts/ isn't a package, same approach as tests/test_ci_evaluate.py.
"""

from __future__ import annotations

import importlib.util
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
    findings = mod._scan_text("commit abc123", "Mirrors JIM-081 from JIMINY-dev")
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
