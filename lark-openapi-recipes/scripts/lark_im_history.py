#!/usr/bin/env python3
"""
Read Lark/Feishu group message history with a tenant_access_token.

  python3 lark_im_history.py --chat-id oc_xxx [--user-id ou_xxx] [--limit 20]

Only messages sent after the bot joined the chat are visible. The bot's app
needs `im:message` (and typically `im:chat:readonly`).
"""
import sys
import json
import argparse

from lark_token import get_token, request


def fetch(token, chat_id, user_id=None, limit=20):
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "sort_type": "ByCreateTimeDesc",
        "page_size": min(max(limit, 1), 50),
    }
    if user_id:
        params["user_id"] = user_id
        params["user_id_type"] = "open_id"
    r = request("GET", "/open-apis/im/v1/messages", token, params=params)
    if r.get("code") != 0:
        sys.exit(f"api error: {r}")
    items = r.get("data", {}).get("items", [])[:limit]
    out = []
    for it in items:
        sender = it.get("sender", {})
        body = it.get("body", {}).get("content", "") or ""
        out.append({
            "message_id": it.get("message_id"),
            "sender_id": sender.get("id"),
            "sender_type": sender.get("sender_type"),
            "create_time": it.get("create_time"),
            "msg_type": it.get("msg_type"),
            "content": body[:500],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", required=True)
    ap.add_argument("--user-id")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    msgs = fetch(get_token(), args.chat_id, args.user_id, args.limit)
    print(json.dumps({"count": len(msgs), "data": msgs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
