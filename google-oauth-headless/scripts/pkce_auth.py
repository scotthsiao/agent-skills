#!/usr/bin/env python3
"""
Headless Google OAuth via manual PKCE — produces a token.json that
google-api-python-client's Credentials.from_authorized_user_file() can load
and refresh.

Why this exists: google-auth-oauthlib's InstalledAppFlow generates the PKCE
code_verifier internally and does not expose it, so splitting "print URL" and
"exchange code" into two processes (unavoidable for an agent with no
interactive stdin) breaks with "invalid_grant: Invalid code verifier". Here we
own the verifier ourselves.

Usage:
  python3 pkce_auth.py stage1                  # print auth URL, persist verifier
  python3 pkce_auth.py stage2 <redirect_url>   # exchange code -> token.json
  python3 pkce_auth.py verify                   # refresh once to prove it works

Env overrides (all optional):
  CLIENT_SECRET   default: ./client_secret.json   (Desktop-app OAuth client)
  STATE_FILE      default: /tmp/oauth_state.json
  TOKEN_FILE      default: ./token.json
  OAUTH_SCOPES    default: https://www.googleapis.com/auth/drive.file
                  (space-separated for multiple)
"""
import os
import sys
import json
import base64
import hashlib
import secrets
import urllib.parse
import urllib.request

CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "./client_secret.json")
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/oauth_state.json")
TOKEN_FILE = os.environ.get("TOKEN_FILE", "./token.json")
SCOPES = os.environ.get("OAUTH_SCOPES", "https://www.googleapis.com/auth/drive.file")
REDIRECT_URI = "http://localhost"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_client():
    with open(CLIENT_SECRET) as f:
        data = json.load(f)
    cfg = data.get("installed") or data.get("web")
    if not cfg:
        sys.exit("client_secret.json has neither an 'installed' nor 'web' block")
    return cfg


def _post_form(url: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def stage1():
    cfg = _load_client()
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    with open(STATE_FILE, "w") as f:
        json.dump({
            "verifier": verifier,
            "state": state,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "token_uri": cfg.get("token_uri", "https://oauth2.googleapis.com/token"),
        }, f)

    print(cfg["auth_uri"] + "?" + urllib.parse.urlencode(params))
    print("\nOpen that URL in any browser, consent, then run:")
    print('  python3 pkce_auth.py stage2 "<the full http://localhost/?code=... URL>"')


def stage2(redirect_url: str):
    with open(STATE_FILE) as f:
        st = json.load(f)
    query = redirect_url.split("?", 1)[1] if "?" in redirect_url else redirect_url
    parsed = urllib.parse.parse_qs(query)
    code = parsed.get("code", [redirect_url])[0]
    if parsed.get("state", [st["state"]])[0] != st["state"]:
        sys.exit("state mismatch — use the exact URL printed by stage1, then retry stage1")

    tok = _post_form(st["token_uri"], {
        "client_id": st["client_id"],
        "client_secret": st["client_secret"],
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": st["verifier"],
    })
    if "access_token" not in tok:
        sys.exit(f"token exchange failed: {tok}")

    out = {
        "token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "token_uri": st["token_uri"],
        "client_id": st["client_id"],
        "client_secret": st["client_secret"],
        "scopes": SCOPES.split(),
        "expiry": None,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {TOKEN_FILE}")
    print(f"refresh_token present: {out['refresh_token'] is not None}")
    if out["refresh_token"] is None:
        print("WARNING: no refresh_token — headless renewal will fail. "
              "Revoke access in your Google account and re-run stage1.")


def verify():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES.split())
    creds.refresh(Request())
    print(f"OK — token refreshes, expires {creds.expiry}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage1"
    if cmd == "stage1":
        stage1()
    elif cmd == "stage2":
        if len(sys.argv) < 3:
            sys.exit("usage: pkce_auth.py stage2 <redirect_url>")
        stage2(sys.argv[2])
    elif cmd == "verify":
        verify()
    else:
        sys.exit(__doc__)
