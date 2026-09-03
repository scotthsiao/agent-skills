#!/usr/bin/env python3
"""
Mint a Lark/Feishu tenant_access_token from app credentials.

Reads FEISHU_APP_ID / FEISHU_APP_SECRET from the environment, or from a dotenv
file named by LARK_ENV_FILE (default: ~/.hermes/.env). Never prints the secret.

  python3 lark_token.py               # prints the token
  from lark_token import get_token, api_host, request   # use as a module

Env:
  FEISHU_APP_ID, FEISHU_APP_SECRET    app credentials (or put them in the dotenv)
  LARK_ENV_FILE                       dotenv to read creds from (default ~/.hermes/.env)
  LARK_DOMAIN                         "lark" (default) or "feishu"
"""
import os
import sys
import json
import urllib.request
import urllib.parse

_DOMAIN = os.environ.get("LARK_DOMAIN", "lark")


def api_host() -> str:
    return "https://open.feishu.cn" if _DOMAIN == "feishu" else "https://open.larksuite.com"


def _load_dotenv():
    path = os.path.expanduser(os.environ.get("LARK_ENV_FILE", "~/.hermes/.env"))
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _creds():
    env = _load_dotenv()
    app_id = os.environ.get("FEISHU_APP_ID") or env.get("FEISHU_APP_ID")
    secret = os.environ.get("FEISHU_APP_SECRET") or env.get("FEISHU_APP_SECRET")
    if not app_id or not secret:
        sys.exit("need FEISHU_APP_ID / FEISHU_APP_SECRET (env or dotenv)")
    return app_id, secret


def get_token() -> str:
    app_id, secret = _creds()
    body = json.dumps({"app_id": app_id, "app_secret": secret}).encode()
    req = urllib.request.Request(
        api_host() + "/open-apis/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    if data.get("code") != 0:
        sys.exit(f"token request failed: {data}")
    return data["tenant_access_token"]


def request(method: str, path: str, token: str, params: dict | None = None,
            body: dict | None = None) -> dict:
    """Call a Lark OpenAPI path (starting with /open-apis/...) and return JSON."""
    url = api_host() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


if __name__ == "__main__":
    print(get_token())
