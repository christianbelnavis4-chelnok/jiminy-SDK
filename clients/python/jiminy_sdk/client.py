"""Jiminy SDK — Client, a thin ergonomic wrapper around POST /evaluate.

Deliberately built on stdlib `urllib.request` rather than `requests`/`httpx`
to keep jiminy_sdk dependency-free (see pyproject.toml: `dependencies = []`),
matching TraceBuilder and CalibrationSession's existing zero-dependency
design. For anything beyond simple evaluate() calls (custom retries,
connection pooling, async), use the generated `jiminy_api_client` package
directly, or bring your own HTTP client and call the REST API as
CalibrationSession.submit() already does.

Usage::

    from jiminy_sdk import Client, TraceBuilder

    client = Client(api_key="...", base_url="https://jiminy-api-...")
    trace = (
        TraceBuilder(...)
        .add_step(1, "tool_name", input=..., output=..., reasoning="...")
        .finalize("final output")
        .build()
    )
    result = client.evaluate(trace)
    print(result["overall_verdict"])
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class JiminyAPIError(Exception):
    """Raised when the Jiminy API returns a non-2xx response.

    Carries the HTTP status code and the parsed (or raw) response body so
    callers can branch on `error_code` without re-parsing JSON themselves.
    """

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        detail = body.get("detail") if isinstance(body, dict) else body
        super().__init__(f"Jiminy API error {status}: {detail}")


class Client:
    """Minimal synchronous client for the Jiminy evaluation API.

    One call to build a self-serve or design-partner client:

        client = Client(api_key="...", base_url="https://jiminy-api-...")
    """

    def __init__(self, *, api_key: str, base_url: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _post(self, path: str, *, json_body: dict, params: dict | None = None) -> dict:
        url = f"{self._base_url}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        data = json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            raise JiminyAPIError(exc.code, body) from exc

    def evaluate(
        self,
        trace: dict,
        *,
        force: bool = False,
        runs: int = 1,
        mode: str = "evaluate",
    ) -> dict:
        """POST a built trace dict (e.g. from TraceBuilder.build()) to /evaluate.

        `trace` is the plain dict returned by TraceBuilder.build() — this
        method does not build or sign the trace itself, it only submits it.

        Set mode="calibrate" for a diagnostic run that isn't persisted and
        doesn't count against quota (see CalibrationSession for a more
        purpose-built wrapper around that flow).

        Raises JiminyAPIError on any non-2xx response.
        """
        params: dict[str, Any] = {}
        if force:
            params["force"] = "true"
        if runs != 1:
            params["runs"] = runs
        if mode != "evaluate":
            params["mode"] = mode
        return self._post("/evaluate", json_body=trace, params=params or None)
