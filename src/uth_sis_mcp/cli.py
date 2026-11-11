"""Command line entry point: run the server, store credentials, or do a quick
end-to-end check against the real site."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import traceback

from .config import KEYRING_SERVICE, ConfigError, Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uth-sis-mcp", description="MCP server for UTH SIS-Web")
    parser.add_argument("-v", "--verbose", action="store_true", help="log requests to stderr")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    sub.add_parser("check", help="log in and read each page once")
    sub.add_parser("login", help="store username and password in the system keyring")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "login":
        return store_credentials()
    if args.command == "check":
        return check()
    from .server import run
    run()
    return 0


def store_credentials() -> int:
    import keyring

    username = input("UTH username (as for eClass, without @uth.gr): ").strip()
    if username.lower().endswith("@uth.gr"):
        username = username.rsplit("@", 1)[0]
        print(f"using {username}")
    password = getpass.getpass("password: ")
    if not username or not password:
        print("nothing stored", file=sys.stderr)
        return 1
    keyring.set_password(KEYRING_SERVICE, "username", username)
    keyring.set_password(KEYRING_SERVICE, username, password)
    print(f"stored credentials for {username} in the system keyring")
    return 0


def check() -> int:
    """Read each thing once and print what came back. Read-only: nothing on
    the site is changed."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    from .client import SisClient

    client = SisClient(settings)
    failures = 0

    def step(label, fn):
        nonlocal failures
        try:
            result = fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                traceback.print_exc()
            return None
        if isinstance(result, list):
            sample = result[0] if result else None
            summary = f"{len(result)} items"
            if sample:
                summary += ", first: " + str(sample.get("title") or sample.get("code") or sample)[:70]
        elif isinstance(result, dict):
            summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(result.items())[:5])
        else:
            summary = str(result)[:80]
        print(f"ok    {label}: {summary}")
        return result

    print(f"SIS-Web: {settings.base_url}  user: {settings.username}  VPN profile: {settings.vpn_profile}")
    if step("login (and VPN)", lambda: client.login() or "logged in") is None:
        print("stopping here: repeated failed logins can get the account blocked for a while")
        return 1
    step("profile", lambda: client.profile().get("departmentTitle"))
    step("student", client.student)
    step("progress", client.progress)
    step("transcript", client.transcript)
    step("grade attempts", client.attempts)
    step("curriculum", client.curriculum)
    periods = step("registration periods", client.periods) or []
    for p in periods:
        if p.get("open_now"):
            print(f"      open now: {p['title']} until {p['open_until']} - {p.get('declaration')}")
    step("current registration", client.registration)
    step("announcements", client.announcements)
    client.close()
    print("all good" if not failures else f"{failures} step(s) failed; run with -v for details")
    return 1 if failures else 0
