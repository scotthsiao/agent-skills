#!/usr/bin/env python3
"""
Crawl a Lark/Feishu wiki subtree with a tenant_access_token.

  # index only (titles + node tokens + links) — cheap, good for large spaces:
  python3 lark_wiki_crawl.py --node <WIKI_TOKEN> --index

  # index + docx bodies (raw_content) for every docx node:
  python3 lark_wiki_crawl.py --node <WIKI_TOKEN> --content

  # start from a space id + parent node instead of a link token:
  python3 lark_wiki_crawl.py --space <SPACE_ID> --parent <NODE_TOKEN> --index

Notes:
  - Children are listed via the `parent_node_token` query param on the /nodes
    endpoint. The /nodes/{node}/children sub-path 404s.
  - Whole-space listing (/wiki/v2/spaces) needs scope the bot usually lacks; this
    script only traverses from a node the bot can already see.
"""
import sys
import json
import time
import argparse

from lark_token import get_token, request, api_host

_DOMAIN_HOST = api_host().split("//", 1)[1]


def get_node(token: str, wiki_token: str) -> dict:
    r = request("GET", "/open-apis/wiki/v2/spaces/get_node", token,
                params={"token": wiki_token, "obj_type": "wiki"})
    if r.get("code") != 0:
        sys.exit(f"get_node failed: {r}")
    return r["data"]["node"]


def list_children(token: str, space_id: str, parent_node_token: str) -> list:
    out, page = [], None
    while True:
        params = {"parent_node_token": parent_node_token, "page_size": 50}
        if page:
            params["page_token"] = page
        r = request("GET", f"/open-apis/wiki/v2/spaces/{space_id}/nodes", token,
                    params=params)
        if r.get("code") != 0:
            sys.stderr.write(f"list_children failed at {parent_node_token}: {r}\n")
            break
        data = r["data"]
        out.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page = data.get("page_token")
        time.sleep(0.2)
    return out


def docx_raw(token: str, obj_token: str) -> str:
    r = request("GET", f"/open-apis/docx/v1/documents/{obj_token}/raw_content", token)
    if r.get("code") != 0:
        return f"[could not read: {r.get('msg')}]"
    text = r["data"].get("content", "")
    return text.replace(" ", " ").replace("​", "")


def wiki_link(node_token: str, obj_type: str, obj_token: str) -> str:
    if obj_type == "docx":
        return f"https://{_DOMAIN_HOST_BRAND()}/docx/{obj_token}"
    return f"https://{_DOMAIN_HOST_BRAND()}/wiki/{node_token}"


def _DOMAIN_HOST_BRAND() -> str:
    # open.larksuite.com -> larksuite.com ; open.feishu.cn -> feishu.cn
    return _DOMAIN_HOST.replace("open.", "")


def walk(token, space_id, parent, depth, want_content, out):
    for node in list_children(token, space_id, parent):
        nt = node["node_token"]
        entry = {
            "depth": depth,
            "title": node.get("title", "(untitled)"),
            "node_token": nt,
            "obj_type": node.get("obj_type"),
            "obj_token": node.get("obj_token"),
            "link": wiki_link(nt, node.get("obj_type"), node.get("obj_token")),
        }
        if want_content and node.get("obj_type") == "docx":
            entry["content"] = docx_raw(token, node["obj_token"])
        out.append(entry)
        if node.get("has_child"):
            walk(token, space_id, nt, depth + 1, want_content, out)
            time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", help="a wiki link token (resolves to space + node)")
    ap.add_argument("--space", help="space_id (with --parent)")
    ap.add_argument("--parent", help="parent node_token (with --space)")
    ap.add_argument("--index", action="store_true", help="titles + links only")
    ap.add_argument("--content", action="store_true", help="also fetch docx bodies")
    args = ap.parse_args()

    token = get_token()
    if args.node:
        n = get_node(token, args.node)
        space_id, parent = n["space_id"], n["node_token"]
        root_title = n.get("title")
    elif args.space and args.parent:
        space_id, parent, root_title = args.space, args.parent, "(root)"
    else:
        ap.error("give --node OR (--space and --parent)")

    result = []
    walk(token, space_id, parent, 0, args.content, result)
    print(json.dumps({"root": root_title, "space_id": space_id,
                      "count": len(result), "nodes": result},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
