"""`jiminy` CLI — `jiminy auth login` (JIM-071) and `jiminy eval` (JIM-076).

`auth login` is device-authorization login: the terminal never sees a
Firebase ID token or a password. It gets a short code, hands it to a
browser, and polls until the browser side confirms — same shape as `gh
auth login` / `az login --use-device-code`.

`eval` hits the unauthenticated public demo endpoint (`POST
/public/demo-eval`, api/routers/demo.py) — no credentials needed, so it's
the thing a first-time visitor runs right after `pip install jiminy-sdk`,
before `auth login` is even relevant. `--demo` evaluates a bundled
reference trace; `--trace <file>` evaluates the caller's own trace. Both
share the same IP-rate-limited daily budget server-side; a 429 here prints
a message pointing at `jiminy auth login` for unlimited use rather than a
raw stack trace.

Stdlib-only throughout, matching client.py's zero-dependency design (see
pyproject.toml: `dependencies = []`).

On success, `auth login` writes ~/.jiminy/credentials.json — the file
examples/first_trace.py and the rest of this SDK read JIMINY_API_KEY /
JIMINY_BASE_URL / JIMINY_TENANT_ID from, as a fallback when those
environment variables aren't set directly.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

_DEFAULT_BASE_URL = "https://jiminy-api-287920422190.europe-west2.run.app"
_CREDENTIALS_PATH = Path.home() / ".jiminy" / "credentials.json"


def _post(
    base_url: str,
    path: str,
    *,
    bearer: str | None = None,
    json_body: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    # Genuinely empty body (not b"{}") when no JSON payload is given: the
    # demo-eval endpoint's `trace: DecisionTrace | None = Body(default=None)`
    # only falls back to its default on an absent body -- posting the
    # literal object `{}` would instead be validated as an (invalid, empty)
    # DecisionTrace and 422. The device-auth endpoints this also serves
    # don't declare a body param at all, so an empty body is a no-op there.
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        detail = body.get("detail") if isinstance(body, dict) else body
        raise _APIError(exc.code, detail) from exc


class _APIError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"API error {status}: {detail}")


def _save_credentials(*, base_url: str, api_key: str, tenant_id: str, tier: str, org_name: str) -> None:
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CREDENTIALS_PATH.write_text(
        json.dumps(
            {
                "base_url": base_url,
                "api_key": api_key,
                "tenant_id": tenant_id,
                "tier": tier,
                "org_name": org_name,
            },
            indent=2,
        )
        + "\n"
    )
    # Contains a live API key — owner read/write only, same posture as an
    # SSH private key or ~/.aws/credentials.
    os.chmod(_CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)


def auth_login(base_url: str) -> int:
    try:
        start = _post(base_url, "/auth/device/start")
    except _APIError as exc:
        print(f"  Could not start login: {exc.detail}", file=sys.stderr)
        return 1
    device_code = start["device_code"]
    user_code = start["user_code"]
    verification_uri = start["verification_uri"]
    verification_uri_complete = start["verification_uri_complete"]
    expires_in = start["expires_in"]
    interval = start["interval"]

    print()
    print("  First, sign in with this code:")
    print()
    print(f"    {user_code}")
    print()
    print(f"  Opening {verification_uri} in your browser...")
    print("  (the code above is pre-filled — just confirm and sign in)")
    print()
    opened = False
    try:
        opened = webbrowser.open(verification_uri_complete)
    except Exception:  # noqa: BLE001
        opened = False
    if not opened:
        print(f"  Could not open a browser automatically. Visit this URL instead:")
        print(f"    {verification_uri_complete}")
        print()

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            poll = _post(base_url, "/auth/device/poll", bearer=device_code)
        except _APIError as exc:
            print(f"  Login failed: {exc.detail}", file=sys.stderr)
            return 1
        if poll["status"] == "fulfilled":
            _save_credentials(
                base_url=base_url,
                api_key=poll["api_key"],
                tenant_id=poll["tenant_id"],
                tier=poll["tier"],
                org_name=poll["org_name"],
            )
            print(f"  Signed in to {poll['org_name']} ({poll['tenant_id']}).")
            print(f"  Credentials saved to {_CREDENTIALS_PATH}")
            print()
            return 0

    print("  Login timed out. Run `jiminy auth login` again.")
    return 1


# --- `jiminy eval` ---------------------------------------------------------

_FINDING_SYMBOLS = {"PASS": "✓", "CONCERN": "⚠", "FAIL": "✗"}
_VERDICT_LABELS = {
    "approved": "APPROVED",
    "flagged": "FLAGGED FOR REVIEW",
    "rejected": "REJECTED",
}
_WIDTH = 70

# ANSI, used only when stdout is a real terminal (see _supports_color) --
# green/amber/red per finding, matching the dashboard's verdict colours,
# with a plain-text fallback so piped/redirected output (and this block
# pasted into a Markdown README) stays readable without escape codes.
_COLOR = {"PASS": "\033[32m", "CONCERN": "\033[33m", "FAIL": "\033[31m", "reset": "\033[0m"}


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _render_verdict(result: dict) -> str:
    """Render an EvaluationResult dict as a scannable pass/fail/review block.

    Mirrors the internal `cli/evaluate.py` renderer's layout (six-criteria
    table + verdict banner) but reads only the public JSON shape returned
    by the API -- this package has no access to (and must not depend on)
    the private judge/renderer code that produces that shape server-side.
    """
    color = _supports_color()
    lines: list[str] = []
    lines.append("=" * _WIDTH)
    lines.append("  JIMINY -- Agent Accountability Evaluation")
    lines.append("=" * _WIDTH)
    lines.append(f"  Trace    : {result.get('trace_id', '?')}")
    lines.append(f"  Model    : {result.get('model_used', '?')}")
    lines.append("-" * _WIDTH)

    for c in result.get("criteria", []):
        finding = c.get("finding", "?")
        sym = _FINDING_SYMBOLS.get(finding, "?")
        label = c.get("label", c.get("criterion", "?"))
        line = f"  {c.get('criterion', '?')}  {label:<28} {sym} {finding:<8}  {c.get('attribution', '')}"
        if color:
            line = f"{_COLOR.get(finding, '')}{line}{_COLOR['reset']}"
        lines.append(line)
        extract = c.get("evidence_extract", "")
        indent = "       "
        while len(extract) > 64:
            cut = extract.rfind(" ", 0, 64)
            if cut == -1:
                cut = 64
            lines.append(f'{indent}"{extract[:cut]}')
            extract = extract[cut:].lstrip()
        lines.append(f'{indent}"{extract}"')
        lines.append("")

    verdict = result.get("overall_verdict", "?")
    verdict_label = _VERDICT_LABELS.get(verdict, verdict.upper())
    lines.append("-" * _WIDTH)
    verdict_line = f"  VERDICT: {verdict_label}"
    if color:
        verdict_color = {"approved": "PASS", "flagged": "CONCERN", "rejected": "FAIL"}.get(
            verdict, ""
        )
        verdict_line = f"{_COLOR.get(verdict_color, '')}{verdict_line}{_COLOR['reset']}"
    lines.append(verdict_line)
    lines.append("=" * _WIDTH)
    return "\n".join(lines)


def eval_command(base_url: str, *, demo: bool, trace_path: str | None) -> int:
    if demo and trace_path:
        print("  Use either --demo or --trace, not both.", file=sys.stderr)
        return 1
    if not demo and not trace_path:
        print("  Usage: jiminy eval --demo | jiminy eval --trace <file.json>", file=sys.stderr)
        return 1

    body: dict | None = None
    if trace_path:
        path = Path(trace_path)
        if not path.exists():
            print(f"  Trace file not found: {trace_path}", file=sys.stderr)
            return 1
        try:
            body = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"  {trace_path} is not valid JSON: {exc}", file=sys.stderr)
            return 1

    try:
        result = _post(base_url, "/public/demo-eval", json_body=body, timeout=90.0)
    except _APIError as exc:
        if exc.status == 429:
            print()
            print("  You've used today's free demo evaluations.")
            print("  Sign up for unlimited use: `jiminy auth login`")
            print()
            return 1
        if exc.status == 413:
            print(f"  {exc.detail}", file=sys.stderr)
            return 1
        print(f"  Evaluation failed ({exc.status}): {exc.detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"  Could not reach {base_url}: {exc.reason}", file=sys.stderr)
        return 1

    print()
    print(_render_verdict(result))
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jiminy")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("JIMINY_BASE_URL", _DEFAULT_BASE_URL),
        help="Jiminy API base URL (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Manage Jiminy authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_subparsers.add_parser("login", help="Sign in via device authorization")

    eval_parser = subparsers.add_parser(
        "eval", help="Run a trace through the judge -- no account required"
    )
    eval_group = eval_parser.add_mutually_exclusive_group()
    eval_group.add_argument(
        "--demo", action="store_true", help="Evaluate a bundled reference trace"
    )
    eval_group.add_argument(
        "--trace", metavar="FILE", help="Evaluate a DecisionTrace JSON file"
    )

    args = parser.parse_args(argv)

    if args.command == "auth" and args.auth_command == "login":
        return auth_login(args.base_url)
    if args.command == "eval":
        return eval_command(args.base_url, demo=args.demo, trace_path=args.trace)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
