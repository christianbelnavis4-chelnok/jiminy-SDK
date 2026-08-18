"""Jiminy SDK — device-authorization login and local credential storage.

Implements the CLI-friendly flow: `jiminy auth login` opens a browser for
Firebase sign-in, polls the API until the user completes it, and stores the
auto-provisioned API key locally so `Client()` and `TraceBuilder` never need
manual `JIMINY_API_KEY`/`JIMINY_TENANT_ID` exports again.

Protocol (mirrors RFC 8628's OAuth Device Authorization Grant):

    POST {base_url}/auth/device/start
      -> {device_code, user_code, verification_url,
          verification_url_complete?, poll_interval, expires_in}

    POST {base_url}/auth/device/poll   {"device_code": "..."}
      -> 200 {"error": "authorization_pending" | "slow_down"}   (still waiting)
      -> 200 {"error": "expired_token" | "access_denied", ...}  (terminal failure)
      -> 200 {api_key, tenant_id, tier, org_name}                (success, no "error" key)

This is not yet implemented server-side; this client is written against the
contract above so the API can be built to match it.
"""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://jiminy-api-REDACTED_PROJECT_NUMBER.europe-west2.run.app"


def credentials_path() -> Path:
    """Where local credentials are stored: ~/.jiminy/credentials.json."""
    override = os.environ.get("JIMINY_CREDENTIALS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".jiminy" / "credentials.json"


def load_credentials() -> dict[str, Any] | None:
    """Read stored credentials, or None if not logged in / file is unreadable."""
    path = credentials_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "api_key" not in data:
        return None
    return data


def save_credentials(data: dict[str, Any]) -> None:
    """Write credentials to disk, restricted to the current user (chmod 600)."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def clear_credentials() -> bool:
    """Delete stored credentials. Returns True if a file was removed."""
    path = credentials_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


class DeviceAuthError(Exception):
    """Raised when the device-authorization flow fails or expires."""


def _post_json(url: str, body: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise DeviceAuthError(f"Jiminy API error {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise DeviceAuthError(f"Could not reach {url}: {exc.reason}") from exc


def login(
    *,
    base_url: str = DEFAULT_BASE_URL,
    org_name: str | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    print_fn: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    max_wait: float = 300.0,
) -> dict[str, Any]:
    """Run the device-authorization flow and persist the resulting credentials.

    Opens the verification URL in a browser, polls until the user completes
    Firebase sign-in, then saves {api_key, tenant_id, base_url, tier,
    org_name} to ~/.jiminy/credentials.json and returns that dict.

    Raises DeviceAuthError on timeout, denial, or an API error.
    """
    base_url = base_url.rstrip("/")
    start = _post_json(f"{base_url}/auth/device/start", {"org_name": org_name} if org_name else {})

    device_code = start["device_code"]
    user_code = start["user_code"]
    verification_url = start["verification_url"]
    poll_interval = float(start.get("poll_interval", 5))
    expires_in = float(start.get("expires_in", max_wait))

    print_fn(f"To sign in, visit: {verification_url}")
    print_fn(f"And enter code: {user_code}")
    open_browser(start.get("verification_url_complete", verification_url))

    deadline = time.monotonic() + min(expires_in, max_wait)
    while time.monotonic() < deadline:
        sleep(poll_interval)
        result = _post_json(f"{base_url}/auth/device/poll", {"device_code": device_code})

        error = result.get("error")
        if error is None:
            credentials = {
                "api_key": result["api_key"],
                "tenant_id": result["tenant_id"],
                "tier": result.get("tier"),
                "org_name": result.get("org_name"),
                "base_url": base_url,
            }
            save_credentials(credentials)
            return credentials
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            poll_interval *= 2
            continue
        raise DeviceAuthError(f"Sign-in failed: {error}")

    raise DeviceAuthError("Sign-in timed out before it was completed in the browser.")
