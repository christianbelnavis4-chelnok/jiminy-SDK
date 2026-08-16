"""Jiminy SDK — CalibrationSession for design partner onboarding."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from jiminy_sdk.builder import TraceBuilder


class CalibrationSession:
    """Wraps TraceBuilder to build an attested trace and submit it in calibrate mode.

    Intended for design partners running their first real traces through the judge
    before going live. Calibration mode evaluations are not persisted; the result
    includes a per-criterion ``calibration_report`` to guide improvements.

    Usage::

        import httpx
        session = CalibrationSession(
            api_base="https://api.jiminy.uk",
            api_key="your-key",
            hmac_key="your-hmac-key",
            trace_id="...",
            agent_id="MyAgent",
            agent_owner="ACME-Corp",
            submitted_by="JIMINY-corp",
            task_description="...",
            domain_profile="general",
        )
        session.add_step(1, "search", input="query", output="results")
        session.finalize("Summary output.")
        result = session.submit(httpx.Client())
        print(result["calibration_report"])
    """

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        hmac_key: str | None = None,
        trace_id: str,
        agent_id: str,
        agent_owner: str,
        submitted_by: str,
        task_description: str,
        domain_profile: str = "general",
        **kwargs: Any,
    ) -> None:
        from datetime import datetime

        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._builder = TraceBuilder(
            trace_id=trace_id,
            agent_id=agent_id,
            agent_owner=agent_owner,
            submitted_by=submitted_by,
            task_description=task_description,
            domain_profile=domain_profile,
            timestamp=kwargs.get("timestamp", datetime.now(UTC)),
            hmac_key=hmac_key or "",
            escalation_events=kwargs.get("escalation_events"),
        )

    def add_step(
        self,
        step_id: int,
        tool: str,
        *,
        input: Any,
        output: Any,
        reasoning: str | None = None,
    ) -> CalibrationSession:
        self._builder.add_step(step_id, tool, input=input, output=output, reasoning=reasoning)
        return self

    def finalize(self, final_output: str) -> CalibrationSession:
        self._builder.finalize(final_output)
        return self

    def submit(self, client: Any) -> dict:
        """POST the trace to ``/evaluate?mode=calibrate`` and return the result dict.

        ``client`` must be an ``httpx.Client`` (or compatible) with a ``.post()`` method.
        Raises ``httpx.HTTPStatusError`` on non-2xx responses.
        """
        trace_dict = self._builder.build()
        response = client.post(
            f"{self._api_base}/evaluate",
            json=trace_dict,
            params={"mode": "calibrate"},
            headers={"X-API-Key": self._api_key},
        )
        response.raise_for_status()
        return response.json()

    def summary(self, result: dict) -> str:
        """Return a human-readable calibration summary from the result dict."""
        report = result.get("calibration_report") or {}
        verdict = result.get("overall_verdict", "unknown")
        confidence = report.get("calibration_confidence", "unknown")
        lines = [
            f"Verdict: {verdict}  |  Confidence: {confidence}",
            "",
        ]
        for note in report.get("criteria_notes", []):
            finding = note.get("finding", "")
            prefix = {"PASS": "✓", "CONCERN": "~", "FAIL": "✗"}.get(finding, "?")
            lines.append(f"  {prefix} [{note.get('criterion')}] {note.get('label')}: {finding}")
        improvements = report.get("suggested_improvements", [])
        if improvements:
            lines += ["", "Suggested improvements:"]
            for item in improvements:
                lines.append(f"  • {item}")
        return "\n".join(lines)
