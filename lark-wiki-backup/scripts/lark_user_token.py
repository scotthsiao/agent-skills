#!/usr/bin/env python3
"""
lark_user_token.py — obtain and auto-refresh a Lark/Feishu user_access_token
for unattended use (LARK_USER_TOKEN_CMD).

user_access_token lasts ~2h. refresh_token lasts ~30 days and ROTATES on every
refresh — this script persists the new one each time.

One-time setup (needs a browser once):
  1. In the Lark developer console for your app:
       - Security settings -> Redirect URLs: add  http://localhost:9899
       - Permissions: grant the read scopes you need, plus `offline_access`
         (required to receive a refresh_token). Publish a version.
  2. python3 lark_user_token.py authorize
       -> open the printed URL, log in, approve. Your browser lands on a
          "can't connect" page at http://localhost:9899/?code=...&state=...
          — that's expected; copy the whole address-bar URL.
  3. python3 lark_user_token.py exchange "http://localhost:9899/?code=...&state=..."
       -> saves the refresh_token, prints the first access_token.

Then, for cron:
  export LARK_USER_TOKEN_CMD='python3 /path/to/lark_user_token.py'
  # each call refreshes and prints ONLY the access_token on stdout.

Env:
  FEISHU_APP_ID / FEISHU_APP_SECRET   app creds (or put them in LARK_ENV_FILE)
  LARK_ENV_FILE       dotenv fallback for the creds   (default: ~/.hermes/.env)
  LARK_DOMAIN         lark | feishu                     (default: lark)
  LARK_USER_CREDS     where the rotating refresh_token is stored
                      (default: ~/.config/lark/user_token.json, chmod 600)
  LARK_OAUTH_SCOPES   space-separated; default covers Drive + docs read + export
  LARK_REDIRECT_URI   must match a console Redirect URL (default: http://localhost:9899)
"""
import os
import sys
import json
import stat
import time
import secrets
import urllib.error
import urllib.parse
import urllib.request

DOMAIN = os.environ.get("LARK_DOMAIN", "lark")
HOST = "https://open.feishu.cn" if DOMAIN == "feishu" else "https://open.larksuite.com"
ENV_FILE = os.path.expanduser(os.environ.get("LARK_ENV_FILE", "~/.hermes/.env"))
CREDS = os.path.expanduser(os.environ.get("LARK_USER_CREDS", "~/.config/lark/user_token.json"))
REDIRECT_URI = os.environ.get("LARK_REDIRECT_URI", "http://localhost:9899")
SCOPES = os.environ.get(
    "LARK_OAUTH_SCOPES",
    "offline_access drive:drive:readonly docx:document:readonly "
    "sheets:spreadsheet:readonly drive:export:readonly",
)
AUTHORIZE_EP = f"{HOST}/open-apis/authen/v2/oauth/authorize"
TOKEN_EP = f"{HOST}/open-apis/authen/v2/oauth/token"


def _dotenv():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _app_creds():
    env = _dotenv()
    cid = os.environ.get("FEISHU_APP_ID") or env.get("FEISHU_APP_ID")
    csec = os.environ.get("FEISHU_APP_SECRET") or env.get("FEISHU_APP_SECRET")
    if not cid or not csec:
        sys.exit("need FEISHU_APP_ID / FEISHU_APP_SECRET (env or LARK_ENV_FILE)")
    return cid, csec


def _post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def _load_creds():
    return json.load(open(CREDS)) if os.path.exists(CREDS) else {}


def _save_creds(data):
    os.makedirs(os.path.dirname(CREDS), exist_ok=True)
    tmp = CREDS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600
    os.replace(tmp, CREDS)


def cmd_authorize():
    cid, _ = _app_creds()
    state = secrets.token_urlsafe(12)
    params = {
        "client_id": cid,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    creds = _load_creds()
    creds["pending_state"] = state          # keep any existing refresh_token
    _save_creds(creds)
    print("Open this URL, log in, and approve:\n")
    print(AUTHORIZE_EP + "?" + urllib.parse.urlencode(params))
    print(f"\nYour browser will fail to load {REDIRECT_URI}/?code=... — that's fine.")
    print("Copy the whole address-bar URL, then run:")
    print('  python3 lark_user_token.py exchange "<that URL>"')


def cmd_exchange(redirect_url):
    cid, csec = _app_creds()
    q = redirect_url.split("?", 1)[1] if "?" in redirect_url else redirect_url
    parsed = urllib.parse.parse_qs(q)
    code = parsed.get("code", [redirect_url])[0]

    resp = _post_json(TOKEN_EP, {
        "grant_type": "authorization_code",
        "client_id": cid,
        "client_secret": csec,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })
    if "access_token" not in resp:
        sys.exit(f"exchange failed: {resp}")
    if not resp.get("refresh_token"):
        sys.exit("no refresh_token returned — add the `offline_access` scope in the "
                 "console, publish a version, and re-run `authorize`.")
    creds = _load_creds()
    creds.pop("pending_state", None)
    creds["refresh_token"] = resp["refresh_token"]
    creds["obtained"] = int(time.time())
    _save_creds(creds)
    print(resp["access_token"])
    print(f"# saved refresh_token to {CREDS}", file=sys.stderr)


def cmd_token():
    cid, csec = _app_creds()
    if not os.path.exists(CREDS):
        sys.exit(f"{CREDS} not found — run `authorize` then `exchange` first")
    creds = _load_creds()
    rt = creds.get("refresh_token")
    if not rt:
        sys.exit("no refresh_token stored — run `authorize` then `exchange`")

    resp = _post_json(TOKEN_EP, {
        "grant_type": "refresh_token",
        "client_id": cid,
        "client_secret": csec,
        "refresh_token": rt,
    })
    if "access_token" not in resp:
        sys.exit(f"refresh failed: {resp}  (refresh_token may be expired — re-authorize)")
    # persist the ROTATED refresh_token
    creds["refresh_token"] = resp.get("refresh_token", rt)
    creds["refreshed"] = int(time.time())
    _save_creds(creds)
    print(resp["access_token"])   # stdout: nothing but the token


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "token"
    if cmd == "authorize":
        cmd_authorize()
    elif cmd == "exchange":
        if len(sys.argv) < 3:
            sys.exit('usage: lark_user_token.py exchange "<redirect URL or code>"')
        cmd_exchange(sys.argv[2])
    elif cmd in ("token", ""):
        cmd_token()
    else:
        sys.exit(__doc__)
