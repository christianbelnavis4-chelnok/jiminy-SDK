"""`jiminy` command-line entry point: `jiminy auth login|status|logout`.

Installed as a console script by pyproject.toml's [project.scripts].
"""

from __future__ import annotations

import argparse
import sys

from jiminy_sdk.auth import DEFAULT_BASE_URL, DeviceAuthError, clear_credentials, load_credentials, login


def _auth_login(args: argparse.Namespace) -> int:
    try:
        credentials = login(base_url=args.base_url, org_name=args.org_name)
    except DeviceAuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Signed in. Tenant: {credentials['tenant_id']}  Tier: {credentials.get('tier')}")
    print("Credentials saved — Client() and TraceBuilder() will pick them up automatically.")
    return 0


def _auth_status(_args: argparse.Namespace) -> int:
    credentials = load_credentials()
    if credentials is None:
        print("Not signed in. Run `jiminy auth login`.")
        return 1
    print(f"Signed in. Tenant: {credentials.get('tenant_id')}  Tier: {credentials.get('tier')}")
    print(f"Base URL: {credentials.get('base_url', DEFAULT_BASE_URL)}")
    return 0


def _auth_logout(_args: argparse.Namespace) -> int:
    removed = clear_credentials()
    print("Signed out." if removed else "Not signed in.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jiminy", description="Jiminy SDK command-line tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Manage Jiminy sign-in credentials.")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)

    login_parser = auth_subparsers.add_parser("login", help="Sign in via browser and save an API key.")
    login_parser.add_argument("--base-url", dest="base_url", default=DEFAULT_BASE_URL)
    login_parser.add_argument("--org-name", dest="org_name", default=None)
    login_parser.set_defaults(func=_auth_login)

    status_parser = auth_subparsers.add_parser("status", help="Show current sign-in status.")
    status_parser.set_defaults(func=_auth_status)

    logout_parser = auth_subparsers.add_parser("logout", help="Remove saved credentials.")
    logout_parser.set_defaults(func=_auth_logout)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
