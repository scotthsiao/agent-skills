#!/usr/bin/env python3
"""
Weekly health check for a headless Google OAuth token.

Loads TOKEN_FILE, forces a refresh against Google, and reports whether the
refresh_token is still good and how much head-room is left before the access
token expires. Exit 0 = healthy, 1 = unhealthy (broken or expiring soon).

Schedule this on its own cron, ideally one day before the job it protects, so a
dead token is caught with time to re-consent.

Usage:
  python3 check_token.py           # human output, exit code signals health
  python3 check_token.py --json    # JSON on stdout

Env:
  TOKEN_FILE    default: ./token.json
  OAUTH_SCOPES  default: https://www.googleapis.com/auth/drive.file  (space-sep)
  ALERT_CMD     optional: shell command executed when unhealthy; the one-line
                status is piped to its stdin (NUL-terminated, use `xargs -0`)
  WARN_SECONDS  default: 600 — warn if the access token expires sooner than this
                after a refresh (a healthy refresh should yield ~3600s)
"""
import os
import sys
import json
import time
import shlex
import subprocess
import datetime as dt

TOKEN_FILE = os.environ.get("TOKEN_FILE", "./token.json")
SCOPES = os.environ.get("OAUTH_SCOPES",
                        "https://www.googleapis.com/auth/drive.file").split()
ALERT_CMD = os.environ.get("ALERT_CMD", "")
WARN_SECONDS = int(os.environ.get("WARN_SECONDS", "600"))


def _alert(line: str):
    if not ALERT_CMD:
        return
    try:
        subprocess.run(ALERT_CMD, shell=True, input=(line + "\0").encode(),
                       timeout=30, check=False)
    except Exception as e:  # noqa: BLE001 - best effort
        print(f"(alert command failed: {e})", file=sys.stderr)


def main() -> int:
    as_json = "--json" in sys.argv
    result = {"token_file": TOKEN_FILE, "healthy": False, "detail": ""}

    if not os.path.exists(TOKEN_FILE):
        result["detail"] = "token file missing"
        _emit(result, as_json)
        _alert(f"Drive token: MISSING ({TOKEN_FILE})")
        return 1

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
    except ImportError:
        result["detail"] = "google-auth not installed (pip install google-auth)"
        _emit(result, as_json)
        return 1

    raw = json.load(open(TOKEN_FILE))
    if not raw.get("refresh_token"):
        result["detail"] = "no refresh_token in token file — re-consent with prompt=consent"
        _emit(result, as_json)
        _alert("Drive token: NO REFRESH TOKEN — headless renewal impossible")
        return 1

    creds = Credentials.from_authorized_user_info(raw, SCOPES)
    try:
        creds.refresh(Request())
    except RefreshError as e:
        result["detail"] = f"refresh failed: {e}"
        _emit(result, as_json)
        _alert(f"Drive token: DEAD — {e}. Re-consent required.")
        return 1

    # Persist the refreshed access token / expiry back
    raw["token"] = creds.token
    if creds.expiry:
        raw["expiry"] = creds.expiry.replace(tzinfo=dt.timezone.utc).isoformat()
    json.dump(raw, open(TOKEN_FILE, "w"), indent=2)

    headroom = None
    if creds.expiry:
        headroom = (creds.expiry.replace(tzinfo=dt.timezone.utc)
                    - dt.datetime.now(dt.timezone.utc)).total_seconds()

    result["healthy"] = True
    result["expiry"] = raw.get("expiry")
    result["headroom_seconds"] = int(headroom) if headroom is not None else None

    if headroom is not None and headroom < WARN_SECONDS:
        result["healthy"] = False
        result["detail"] = f"access token expires in {int(headroom)}s (< {WARN_SECONDS})"
        _emit(result, as_json)
        _alert(f"Drive token: refresh OK but short-lived ({int(headroom)}s) — investigate")
        return 1

    result["detail"] = "refresh OK"
    _emit(result, as_json)
    return 0


def _emit(result: dict, as_json: bool):
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        state = "HEALTHY" if result["healthy"] else "UNHEALTHY"
        print(f"[{state}] {result['detail']}")
        if result.get("headroom_seconds") is not None:
            print(f"  access token headroom: {result['headroom_seconds']}s")


if __name__ == "__main__":
    sys.exit(main())
