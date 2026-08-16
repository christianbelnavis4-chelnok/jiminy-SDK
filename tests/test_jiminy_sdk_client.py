"""Tests for jiminy_sdk.Client — the SDK's evaluate() wrapper.

Mocks urllib.request.urlopen rather than hitting a real server — Client is
a thin, dependency-free wrapper, so these tests exist to pin its
request-building and error-handling behaviour.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))

from jiminy_sdk.client import Client, JiminyAPIError  # noqa: E402


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _trace() -> dict:
    return {
        "trace_id": "t-1",
        "agent_id": "a-1",
        "agent_owner": "Acme",
        "submitted_by": "tenant-1",
        "task_description": "test",
        "timestamp": "2026-07-26T00:00:00Z",
        "domain_profile": "general",
        "steps": [{"step_id": 1, "tool": "x", "input": "i", "output": "o"}],
        "final_output": "done",
    }


class TestEvaluate:
    def test_sends_api_key_header_and_json_body(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"overall_verdict": "approved"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        client = Client(api_key="my-key", base_url="https://api.example.com")
        result = client.evaluate(_trace())

        assert result["overall_verdict"] == "approved"
        assert captured["url"] == "https://api.example.com/evaluate"
        assert captured["headers"]["X-api-key"] == "my-key"
        assert captured["body"]["trace_id"] == "t-1"

    def test_query_params_appended_for_non_default_options(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse({"overall_verdict": "approved"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        client = Client(api_key="k", base_url="https://api.example.com")
        client.evaluate(_trace(), force=True, runs=3, mode="calibrate")

        assert "force=true" in captured["url"]
        assert "runs=3" in captured["url"]
        assert "mode=calibrate" in captured["url"]

    def test_no_query_string_for_defaults(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse({"overall_verdict": "approved"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        client = Client(api_key="k", base_url="https://api.example.com")
        client.evaluate(_trace())

        assert captured["url"] == "https://api.example.com/evaluate"

    def test_http_error_raises_jiminy_api_error_with_parsed_body(self, monkeypatch):
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(json.dumps({"detail": "Invalid API key."}).encode()),
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        client = Client(api_key="bad-key", base_url="https://api.example.com")
        with pytest.raises(JiminyAPIError) as exc_info:
            client.evaluate(_trace())

        assert exc_info.value.status == 403
        assert exc_info.value.body["detail"] == "Invalid API key."

    def test_base_url_trailing_slash_stripped(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse({"overall_verdict": "approved"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        client = Client(api_key="k", base_url="https://api.example.com/")
        client.evaluate(_trace())

        assert captured["url"] == "https://api.example.com/evaluate"
