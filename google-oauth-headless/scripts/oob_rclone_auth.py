#!/usr/bin/env python3
"""
Headless Google Drive auth for rclone, via the out-of-band (OOB) code flow.

Prints a consent URL; you open it in any browser, approve, and Google shows a
`code` on screen. Paste it back and this writes an [gdrive] remote into
rclone.conf. No localhost callback, so it works over SSH / inside a container.

Usage:
  GD_CLIENT_ID=...  GD_CLIENT_SECRET=...  python3 oob_rclone_auth.py url
  GD_CLIENT_ID=...  GD_CLIENT_SECRET=...  python3 oob_rclone_auth.py token <CODE>

Env:
  GD_CLIENT_ID       Google OAuth client_id  (Desktop-app client)
  GD_CLIENT_SECRET   Google OAuth client_secret
  RCLONE_CONF        default: ~/.config/rclone/rclone.conf
  RCLONE_REMOTE      default: gdrive
  GD_SCOPE           default: https://www.googleapis.com/auth/drive

Note: Google is phasing OOB out for newer clients. If `url` returns
"invalid_request", fall back to pkce_auth.py (localhost flow) and point rclone at
the resulting token, or use a service account.
"""
import os
import re
import sys
import json
import time
import urllib.parse
import urllib.request

CLIENT_ID = os.environ.get("GD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GD_CLIENT_SECRET", "")
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"
SCOPE = os.environ.get("GD_SCOPE", "https://www.googleapis.com/auth/drive")
AUTH_EP = "https://accounts.google.com/o/oauth2/auth"
TOKEN_EP = "https://oauth2.googleapis.com/token"
RCLONE_CONF = os.path.expanduser(
    os.environ.get("RCLONE_CONF", "~/.config/rclone/rclone.conf"))
REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")


def _need_creds():
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit("set GD_CLIENT_ID and GD_CLIENT_SECRET")


def print_url():
    _need_creds()
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    print("Open this URL, approve, and copy the code Google shows you:\n")
    print(AUTH_EP + "?" + urllib.parse.urlencode(params))
    print(f"\nThen: python3 oob_rclone_auth.py token <CODE>")


def exchange(code: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
    }).encode()
    req = urllib.request.Request(
        TOKEN_EP, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def write_rclone(tok: dict):
    if "access_token" not in tok:
        sys.exit(f"exchange failed: {tok}")
    token_blob = {
        "access_token": tok["access_token"],
        "token_type": tok.get("token_type", "Bearer"),
        "refresh_token": tok.get("refresh_token"),
        "expiry": "",
    }
    if tok.get("expires_in"):
        token_blob["expiry"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000000000Z",
            time.gmtime(time.time() + tok["expires_in"]))
    section = (
        f"[{REMOTE}]\n"
        f"type = drive\n"
        f"scope = drive\n"
        f"client_id = {CLIENT_ID}\n"
        f"client_secret = {CLIENT_SECRET}\n"
        f"token = {json.dumps(token_blob)}\n"
    )
    os.makedirs(os.path.dirname(RCLONE_CONF), exist_ok=True)
    existing = open(RCLONE_CONF).read() if os.path.exists(RCLONE_CONF) else ""
    existing = re.sub(rf"\[{re.escape(REMOTE)}\][^\[]*", "", existing)
    open(RCLONE_CONF, "w").write(existing.rstrip() + "\n\n" + section)
    print(f"wrote [{REMOTE}] to {RCLONE_CONF}")
    print(f"refresh_token present: {bool(tok.get('refresh_token'))}")
    print(f"verify with: rclone lsd {REMOTE}:")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "url":
        print_url()
    elif sys.argv[1] == "token" and len(sys.argv) >= 3:
        _need_creds()
        write_rclone(exchange(sys.argv[2].strip()))
    else:
        sys.exit(__doc__)
