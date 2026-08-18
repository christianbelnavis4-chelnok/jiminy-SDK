"""Tests for jiminy_sdk.auth — device-authorization login and credential storage."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))

from jiminy_sdk.auth import (  # noqa: E402
    DeviceAuthError,
    clear_credentials,
    load_credentials,
    login,
    save_credentials,
)


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMINY_CREDENTIALS_PATH", str(tmp_path / "credentials.json"))


class TestCredentialsStorage:
    def test_load_returns_none_when_no_file(self):
        assert load_credentials() is None

    def test_save_then_load_round_trips(self):
        save_credentials({"api_key": "k", "tenant_id": "t"})
        assert load_credentials() == {"api_key": "k", "tenant_id": "t"}

    def test_save_restricts_file_permissions(self):
        save_credentials({"api_key": "k", "tenant_id": "t"})
        from jiminy_sdk.auth import credentials_path

        mode = credentials_path().stat().st_mode & 0o777
        assert mode == 0o600

    def test_load_returns_none_for_malformed_json(self):
        from jiminy_sdk.auth import credentials_path

        path = credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        assert load_credentials() is None

    def test_load_returns_none_when_api_key_missing(self):
        from jiminy_sdk.auth import credentials_path

        path = credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tenant_id": "t"}))
        assert load_credentials() is None

    def test_clear_removes_file_and_reports_whether_one_existed(self):
        assert clear_credentials() is False
        save_credentials({"api_key": "k", "tenant_id": "t"})
        assert clear_credentials() is True
        assert load_credentials() is None


class TestLogin:
    def _responses(self, monkeypatch, sequence):
        calls = {"n": 0}

        def fake_post_json(url, body, timeout=30.0):
            calls["n"] += 1
            return sequence[calls["n"] - 1]

        monkeypatch.setattr("jiminy_sdk.auth._post_json", fake_post_json)
        return calls

    def test_success_saves_and_returns_credentials(self, monkeypatch):
        self._responses(
            monkeypatch,
            [
                {
                    "device_code": "dc-1",
                    "user_code": "ABCD-1234",
                    "verification_url": "https://app.jiminy.uk/cli-auth",
                    "poll_interval": 0,
                    "expires_in": 60,
                },
                {"error": "authorization_pending"},
                {"api_key": "sk-live-1", "tenant_id": "self-acme-1", "tier": "starter", "org_name": "Acme"},
            ],
        )
        opened = []
        result = login(
            base_url="https://api.example.com",
            open_browser=lambda url: opened.append(url) or True,
            print_fn=lambda *_: None,
            sleep=lambda _: None,
        )

        assert result["api_key"] == "sk-live-1"
        assert result["tenant_id"] == "self-acme-1"
        assert opened == ["https://app.jiminy.uk/cli-auth"]
        assert load_credentials()["api_key"] == "sk-live-1"

    def test_access_denied_raises_and_does_not_save(self, monkeypatch):
        self._responses(
            monkeypatch,
            [
                {
                    "device_code": "dc-1",
                    "user_code": "ABCD-1234",
                    "verification_url": "https://app.jiminy.uk/cli-auth",
                    "poll_interval": 0,
                    "expires_in": 60,
                },
                {"error": "access_denied"},
            ],
        )
        with pytest.raises(DeviceAuthError, match="access_denied"):
            login(
                base_url="https://api.example.com",
                open_browser=lambda _url: True,
                print_fn=lambda *_: None,
                sleep=lambda _: None,
            )
        assert load_credentials() is None

    def test_timeout_raises_when_deadline_passes_before_success(self, monkeypatch):
        self._responses(
            monkeypatch,
            [
                {
                    "device_code": "dc-1",
                    "user_code": "ABCD-1234",
                    "verification_url": "https://app.jiminy.uk/cli-auth",
                    "poll_interval": 0,
                    "expires_in": 0,
                },
            ],
        )
        with pytest.raises(DeviceAuthError, match="timed out"):
            login(
                base_url="https://api.example.com",
                open_browser=lambda _url: True,
                print_fn=lambda *_: None,
                sleep=lambda _: None,
            )
