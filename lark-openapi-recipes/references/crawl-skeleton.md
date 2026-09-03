# Minimal wiki crawl skeleton (stdlib only)

When `scripts/lark_wiki_crawl.py` isn't available (e.g. running inside a sandbox
where `lark-cli` reports `not configured` and you can't import the helper), paste
this. Pure `urllib`, no dependencies.

```python
import json, time, urllib.request, urllib.parse

HOST = "https://open.larksuite.com"          # or open.feishu.cn
APP_ID, APP_SECRET = "cli_xxx", "xxx"         # from your .env — read, don't hardcode in real use

def _call(method, path, token=None, params=None, body=None):
    url = HOST + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def token():
    r = _call("POST", "/open-apis/auth/v3/tenant_access_token/internal",
              body={"app_id": APP_ID, "app_secret": APP_SECRET})
    return r["tenant_access_token"]

def get_node(tok, wiki_token):
    return _call("GET", "/open-apis/wiki/v2/spaces/get_node", tok,
                 params={"token": wiki_token, "obj_type": "wiki"})["data"]["node"]

def children(tok, space_id, parent):
    out, page = [], None
    while True:
        p = {"parent_node_token": parent, "page_size": 50}
        if page: p["page_token"] = page
        d = _call("GET", f"/open-apis/wiki/v2/spaces/{space_id}/nodes", tok, params=p)["data"]
        out += d.get("items", [])
        if not d.get("has_more"): break
        page = d.get("page_token"); time.sleep(0.2)
    return out

def raw(tok, obj_token):
    d = _call("GET", f"/open-apis/docx/v1/documents/{obj_token}/raw_content", tok)
    return d.get("data", {}).get("content", "").replace(" ", " ").replace("​", "")

def walk(tok, space_id, parent, depth=0):
    for n in children(tok, space_id, parent):
        print("  " * depth + f"- {n['title']}  ({n.get('obj_type')})  {n['node_token']}")
        if n.get("obj_type") == "docx":
            pass  # body = raw(tok, n["obj_token"])
        if n.get("has_child"):
            walk(tok, space_id, n["node_token"], depth + 1)

tok = token()
root = get_node(tok, "<WIKI_LINK_TOKEN>")
walk(tok, root["space_id"], root["node_token"])
```

## Scale note

A real knowledge base can be huge (thousands of nodes, most of them under a few
deep "project → test plan" branches). Don't fetch every body:

1. First pass — write an **index only**: title + `node_token` + Lark link, into one
   file per top-level branch.
2. Second pass — fetch bodies for a **curated** short list of high-value branches
   (methodology, decisions, durable references). Leave transient/flowing pages as
   index entries.

Link formats:
- wiki page: `https://<brand-host>/wiki/<node_token>`
- docx page: `https://<brand-host>/docx/<obj_token>`

where `<brand-host>` is `larksuite.com` or `feishu.cn` (drop the `open.` prefix).
