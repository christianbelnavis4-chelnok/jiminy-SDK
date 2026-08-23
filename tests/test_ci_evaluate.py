"""Tests for scripts/ci_evaluate.py — the CI hook (docs/SELF_SERVE_SDK_SPEC.md,
Sprint 2). Loaded via importlib since scripts/ isn't a package, same
approach as tests/test_kpi_report.py and tests/test_seed_broken_attestation.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ci_evaluate.py"
    spec = importlib.util.spec_from_file_location("ci_evaluate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_evaluate"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _write_trace(tmp_path, name, trace_id="t-1"):
    trace = {
        "trace_id": trace_id,
        "agent_id": "a-1",
        "agent_owner": "Acme",
        "submitted_by": "tenant-1",
        "task_description": "test",
        "timestamp": "2026-07-26T00:00:00Z",
        "domain_profile": "general",
        "steps": [{"step_id": 1, "tool": "t", "input": "i", "output": "o"}],
        "final_output": "done",
    }
    path = tmp_path / name
    path.write_text(json.dumps(trace))
    return path


class TestRun:
    def test_all_approved_exits_zero(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")
        _write_trace(tmp_path, "t2.json", "t-2")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "approved", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert exit_code == 0
        assert len(rows) == 2
        assert all(r["verdict"] == "approved" for r in rows)

    def test_rejected_fails_build_by_default(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "rejected", "failed_criteria": ["C1"]}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert exit_code == 1
        assert rows[0]["failed_criteria"] == ["C1"]

    def test_flagged_does_not_fail_by_default(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "flagged", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert exit_code == 0

    def test_fail_on_flagged_is_stricter(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "flagged", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="flagged",
                calibrate=False,
            )
        assert exit_code == 1

    def test_api_error_fails_build_and_is_reported(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")
        from jiminy_sdk import JiminyAPIError

        def fake_evaluate(self, trace, **kwargs):
            raise JiminyAPIError(403, {"detail": "Invalid API key."})

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="bad",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert exit_code == 1
        assert rows[0]["verdict"] == "ERROR"

    def test_calibrate_mode_passed_through(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")
        captured_modes = []

        def fake_evaluate(self, trace, mode="evaluate", **kwargs):
            captured_modes.append(mode)
            return {"overall_verdict": "approved", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=True,
            )
        assert captured_modes == ["calibrate"]

    def test_no_matching_files_exits_zero_with_warning(self, tmp_path, mod, capsys):
        exit_code, rows, fixture_rows, ci_quota = mod.run(
            api_key="k",
            base_url="https://api.example.com",
            traces_glob=str(tmp_path / "*.json"),
            fail_on="rejected",
            calibrate=False,
        )
        assert exit_code == 0
        assert rows == []
        assert "::warning::" in capsys.readouterr().out


class TestFailOnRegression:
    def _fixture(self, expected_finding="FAIL", expected_criterion="C2"):
        return {
            "fixture_id": "fx-1",
            "criterion": "C2",
            "scenario": {
                "synthetic_trace": {"trace_id": "synthetic-1"},
                "expected_criterion": expected_criterion,
                "expected_finding": expected_finding,
                "narrative": "n",
            },
        }

    def test_disabled_by_default_no_fixture_calls(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "approved", "failed_criteria": []}

        with (
            patch("jiminy_sdk.client.Client.evaluate", fake_evaluate),
            patch("jiminy_sdk.client.Client.list_fixtures") as mock_list,
        ):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        mock_list.assert_not_called()
        assert fixture_rows == []

    def test_regression_detected_reported_but_never_fails_build(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, mode="evaluate", **kwargs):
            if trace.get("trace_id") == "synthetic-1":
                # Current build now PASSES what used to FAIL -- a regression.
                return {
                    "overall_verdict": "approved",
                    "failed_criteria": [],
                    "calibration_report": {
                        "criteria_notes": [{"criterion": "C2", "finding": "PASS"}]
                    },
                }
            return {"overall_verdict": "approved", "failed_criteria": []}

        with (
            patch("jiminy_sdk.client.Client.evaluate", fake_evaluate),
            patch(
                "jiminy_sdk.client.Client.list_fixtures",
                return_value=[self._fixture()],
            ),
        ):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
                fail_on_regression=True,
            )

        assert exit_code == 0  # advisory only -- never gates the build
        assert len(fixture_rows) == 1
        assert fixture_rows[0]["regressed"] is True

    def test_fixture_still_holds_reported_as_not_regressed(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, mode="evaluate", **kwargs):
            if trace.get("trace_id") == "synthetic-1":
                return {
                    "overall_verdict": "rejected",
                    "failed_criteria": ["C2"],
                    "calibration_report": {
                        "criteria_notes": [{"criterion": "C2", "finding": "FAIL"}]
                    },
                }
            return {"overall_verdict": "approved", "failed_criteria": []}

        with (
            patch("jiminy_sdk.client.Client.evaluate", fake_evaluate),
            patch(
                "jiminy_sdk.client.Client.list_fixtures",
                return_value=[self._fixture()],
            ),
        ):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
                fail_on_regression=True,
            )

        assert fixture_rows[0]["regressed"] is False

    def test_fixture_load_error_reported_as_warning_not_failure(
        self, tmp_path, mod, capsys
    ):
        from jiminy_sdk import JiminyAPIError

        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "approved", "failed_criteria": []}

        def fake_list_fixtures(self, agent_owner, status="active"):
            raise JiminyAPIError(403, {"detail": "not entitled"})

        with (
            patch("jiminy_sdk.client.Client.evaluate", fake_evaluate),
            patch("jiminy_sdk.client.Client.list_fixtures", fake_list_fixtures),
        ):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
                fail_on_regression=True,
            )

        assert exit_code == 0
        assert fixture_rows == []
        assert "::warning::" in capsys.readouterr().out


class TestFindPrNumber:
    def test_pull_request_event_returns_number(self, tmp_path, mod):
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps({"pull_request": {"number": 42}}))
        assert mod.find_pr_number(str(event_path)) == 42

    def test_push_event_returns_none(self, tmp_path, mod):
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps({"ref": "refs/heads/main"}))
        assert mod.find_pr_number(str(event_path)) is None

    def test_missing_event_path_returns_none(self, mod):
        assert mod.find_pr_number("/nonexistent/path.json") is None

    def test_no_arg_falls_back_to_env(self, tmp_path, mod, monkeypatch):
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps({"pull_request": {"number": 7}}))
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        assert mod.find_pr_number() == 7


class TestPostOrUpdatePrComment:
    _ROWS = [
        {"path": "t1.json", "trace_id": "t-1", "verdict": "rejected", "failed_criteria": ["C1"]}
    ]

    def test_no_rows_does_nothing(self, mod):
        with patch("ci_evaluate._github_request") as mock_request:
            mod.post_or_update_pr_comment(
                [], github_token="tok", repo="acme/agent", pr_number=1
            )
        mock_request.assert_not_called()

    def test_posts_new_comment_when_none_exists(self, mod):
        calls = []

        def fake_request(method, url, token, body=None):
            calls.append((method, url, body))
            if method == "GET":
                return []
            return {"id": 999}

        with patch("ci_evaluate._github_request", side_effect=fake_request):
            mod.post_or_update_pr_comment(
                self._ROWS, github_token="tok", repo="acme/agent", pr_number=5
            )
        assert calls[0][0] == "GET"
        assert calls[1] == (
            "POST",
            "https://api.github.com/repos/acme/agent/issues/5/comments",
            {"body": calls[1][2]["body"]},
        )
        assert mod._PR_COMMENT_MARKER in calls[1][2]["body"]
        assert "t-1" in calls[1][2]["body"]

    def test_updates_existing_comment_in_place(self, mod):
        calls = []

        def fake_request(method, url, token, body=None):
            calls.append((method, url, body))
            if method == "GET":
                return [{"id": 123, "body": f"{mod._PR_COMMENT_MARKER}\nold results"}]
            return {}

        with patch("ci_evaluate._github_request", side_effect=fake_request):
            mod.post_or_update_pr_comment(
                self._ROWS, github_token="tok", repo="acme/agent", pr_number=5
            )
        assert calls[1][0] == "PATCH"
        assert calls[1][1] == "https://api.github.com/repos/acme/agent/issues/comments/123"

    def test_ignores_comments_without_marker(self, mod):
        calls = []

        def fake_request(method, url, token, body=None):
            calls.append((method, url, body))
            if method == "GET":
                return [{"id": 1, "body": "unrelated human comment"}]
            return {}

        with patch("ci_evaluate._github_request", side_effect=fake_request):
            mod.post_or_update_pr_comment(
                self._ROWS, github_token="tok", repo="acme/agent", pr_number=5
            )
        assert calls[1][0] == "POST"

    def test_http_error_is_a_warning_not_an_exception(self, mod, capsys):
        import io
        import urllib.error

        def fake_request(method, url, token, body=None):
            raise urllib.error.HTTPError(
                url, 403, "Forbidden", {}, io.BytesIO(b'{"message": "Forbidden"}')
            )

        with patch("ci_evaluate._github_request", side_effect=fake_request):
            mod.post_or_update_pr_comment(
                self._ROWS, github_token="tok", repo="acme/agent", pr_number=5
            )  # must not raise
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "forks" in out


class TestWriteSummary:
    def test_writes_markdown_table(self, tmp_path, mod):
        rows = [
            {"path": "t1.json", "trace_id": "t-1", "verdict": "approved", "failed_criteria": []},
            {"path": "t2.json", "trace_id": "t-2", "verdict": "rejected", "failed_criteria": ["C1"]},
        ]
        summary_path = tmp_path / "summary.md"
        mod.write_summary(rows, str(summary_path))
        content = summary_path.read_text()
        assert "t-1" in content
        assert "t-2" in content
        assert "C1" in content
        assert "✅" in content
        assert "❌" in content

    def test_empty_rows_writes_nothing(self, tmp_path, mod):
        summary_path = tmp_path / "summary.md"
        mod.write_summary([], str(summary_path))
        assert not summary_path.exists()

    def test_fixture_rows_appended_as_second_table(self, tmp_path, mod):
        rows = [
            {"path": "t1.json", "trace_id": "t-1", "verdict": "approved", "failed_criteria": []}
        ]
        fixture_rows = [
            {"fixture_id": "fx-1", "agent_owner": "Acme", "criterion": "C2", "regressed": True},
            {"fixture_id": "fx-2", "agent_owner": "Acme", "criterion": "C5", "regressed": False},
        ]
        summary_path = tmp_path / "summary.md"
        mod.write_summary(rows, str(summary_path), fixture_rows)
        content = summary_path.read_text()
        assert "fx-1" in content and "fx-2" in content
        assert "Regressed vs frozen" in content
        assert "⚠️" in content  # fx-1 regressed
        assert "✅" in content  # fx-2 still holds

    def test_no_fixture_rows_omits_second_table(self, tmp_path, mod):
        rows = [
            {"path": "t1.json", "trace_id": "t-1", "verdict": "approved", "failed_criteria": []}
        ]
        summary_path = tmp_path / "summary.md"
        mod.write_summary(rows, str(summary_path), [])
        content = summary_path.read_text()
        assert "Regressed vs frozen" not in content

    def test_ci_quota_line_included_when_present(self, tmp_path, mod):
        rows = [
            {"path": "t1.json", "trace_id": "t-1", "verdict": "approved", "failed_criteria": []}
        ]
        summary_path = tmp_path / "summary.md"
        mod.write_summary(rows, str(summary_path), ci_quota={"used": "12", "limit": "500"})
        content = summary_path.read_text()
        assert "CI credit usage" in content
        assert "12/500" in content

    def test_ci_quota_omitted_when_none(self, tmp_path, mod):
        rows = [
            {"path": "t1.json", "trace_id": "t-1", "verdict": "approved", "failed_criteria": []}
        ]
        summary_path = tmp_path / "summary.md"
        mod.write_summary(rows, str(summary_path))
        content = summary_path.read_text()
        assert "CI credit usage" not in content


class TestRunCapturesCiQuota:
    def test_ci_quota_captured_from_response_headers(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            self.last_response_headers = {
                "X-CI-Quota-Used": "3",
                "X-CI-Quota-Limit": "500",
            }
            return {"overall_verdict": "approved", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert exit_code == 0
        assert ci_quota == {"used": "3", "limit": "500"}

    def test_ci_quota_none_when_headers_absent(self, tmp_path, mod):
        _write_trace(tmp_path, "t1.json", "t-1")

        def fake_evaluate(self, trace, **kwargs):
            return {"overall_verdict": "approved", "failed_criteria": []}

        with patch("jiminy_sdk.client.Client.evaluate", fake_evaluate):
            exit_code, rows, fixture_rows, ci_quota = mod.run(
                api_key="k",
                base_url="https://api.example.com",
                traces_glob=str(tmp_path / "*.json"),
                fail_on="rejected",
                calibrate=False,
            )
        assert ci_quota is None
