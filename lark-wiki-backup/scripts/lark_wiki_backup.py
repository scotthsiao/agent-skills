#!/usr/bin/env python3
"""
lark_wiki_backup.py — incremental (delta) mirror of a Lark/Feishu wiki space
to local .docx files, git-committed.

Per node, keyed by node_token in .manifest.json:
  - not tracked, file absent          -> download
  - last_edit_time newer than manifest -> re-download
  - last_edit_time same, path changed  -> os.rename locally (no re-fetch)
  - unchanged                          -> skip
  - tracked but not in this run's tree -> upstream-deleted -> move to .trash/

Standard library only. Reads FEISHU_APP_ID / FEISHU_APP_SECRET from the
environment or LARK_ENV_FILE (a dotenv).

Env:
  LARK_SPACE_ID       (required) wiki space id
  BACKUP_ROOT         output dir, should be a git repo  (default: ./lark-wiki-backup-data)
  LARK_ENV_FILE       dotenv with the app creds         (default: ~/.hermes/.env)
  LARK_DOMAIN         lark | feishu                      (default: lark)
  LARK_EXCLUDE_PATHS  comma-sep title segments to skip entirely
  LARK_TREE_FILE      optional markdown tree snapshot to read the node list from
  LARK_GIT_COMMIT     1 (default) | 0
  LARK_NODE_TYPES     comma-sep obj_types to export      (default: docx)
"""
import os
import re
import sys
import json
import time
import subprocess
import urllib.error
import urllib.request

SPACE_ID = os.environ.get("LARK_SPACE_ID", "")
ROOT = os.path.abspath(os.environ.get("BACKUP_ROOT", "./lark-wiki-backup-data"))
ENV_FILE = os.path.expanduser(os.environ.get("LARK_ENV_FILE", "~/.hermes/.env"))
DOMAIN = os.environ.get("LARK_DOMAIN", "lark")
EXCLUDE_PATHS = {s.strip() for s in os.environ.get("LARK_EXCLUDE_PATHS", "").split(",") if s.strip()}
TREE_FILE = os.environ.get("LARK_TREE_FILE", "")
GIT_COMMIT = os.environ.get("LARK_GIT_COMMIT", "1") == "1"
NODE_TYPES = {s.strip() for s in os.environ.get("LARK_NODE_TYPES", "docx").split(",") if s.strip()}

HOST = "https://open.feishu.cn" if DOMAIN == "feishu" else "https://open.larksuite.com"
MANIFEST = os.path.join(ROOT, ".manifest.json")
TRASH_DIR = os.path.join(ROOT, ".trash")
REQUEST_DELAY = 0.15
BACKOFF_MAX = 120


# --------------------------------------------------------------------------- io
def load_creds():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    app_id = os.environ.get("FEISHU_APP_ID") or env.get("FEISHU_APP_ID")
    secret = os.environ.get("FEISHU_APP_SECRET") or env.get("FEISHU_APP_SECRET")
    if not app_id or not secret:
        sys.exit("need FEISHU_APP_ID / FEISHU_APP_SECRET (env or LARK_ENV_FILE)")
    return app_id, secret


def call(url, token=None, method="GET", data=None, retries=6):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    last_err, backoff = None, 2
    for attempt in range(retries):
        if backoff > BACKOFF_MAX:
            break
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            if data is not None:
                req.data = json.dumps(data).encode()
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = {}
            if body.get("code") == 99991400:  # rate limited
                wait = min(8 + attempt * 6, 60)
                print(f"[rate-limit] +{wait}s ({attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                backoff = min(backoff * 2, 60)
                continue
            last_err = (e.code, body)
            time.sleep(2 + attempt * 2)
        except Exception as e:  # noqa: BLE001
            last_err = ("exc", str(e))
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
    return last_err if last_err else (0, {})


def get_token():
    app_id, secret = load_creds()
    s, c = call(f"{HOST}/open-apis/auth/v3/tenant_access_token/internal",
                method="POST", data={"app_id": app_id, "app_secret": secret})
    if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
        sys.exit(f"token request failed: {s} {c}")
    return c["tenant_access_token"]


# ------------------------------------------------------------------- node list
def safe_filename(title, max_len=80):
    title = re.sub(r'[\\/:*?"<>|]', "_", title).strip(". ")
    return (title[:max_len].rstrip() or "untitled")


def crawl_live(token, space_id):
    """Depth-first crawl of the whole space via the nodes endpoint.
    Returns a flat list of {token,title,depth,parent}."""
    items = []

    def children(parent_token):
        out, page = [], None
        while True:
            url = (f"{HOST}/open-apis/wiki/v2/spaces/{space_id}/nodes"
                   f"?page_size=50&parent_node_token={parent_token}"
                   + (f"&page_token={page}" if page else ""))
            s, c = call(url, token=token)
            if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
                print(f"[crawl] list failed at {parent_token or 'root'}: {s} {c}",
                      file=sys.stderr)
                return out
            data = c.get("data", {})
            out.extend(data.get("items", []))
            if not data.get("has_more"):
                return out
            page = data.get("page_token")
            time.sleep(0.2)

    def walk(parent_token, depth):
        for n in children(parent_token):
            items.append({"token": n["node_token"], "title": n.get("title", "untitled"),
                          "depth": depth, "parent": parent_token or None})
            if n.get("has_child"):
                walk(n["node_token"], depth + 1)
                time.sleep(0.2)

    walk("", 0)
    return items


def parse_tree_md(path):
    items, stack = [], []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(\s*)- .*\[([^\]]+)\]\(https?://[^/]+/wiki/(?:[^/]+/)?([^)]+)\)", line)
        if not m:
            continue
        depth = len(m.group(1)) // 2
        title, token = m.group(2), m.group(3)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        items.append({"token": token, "title": title, "depth": depth, "parent": parent})
        stack.append((depth, token))
    return items


def build_path_map(items):
    kids = {}
    for it in items:
        kids.setdefault(it.get("parent"), []).append(it)
    out = {}

    def assign(parent, parts):
        for ch in kids.get(parent, []):
            safe = safe_filename(ch["title"], 60)
            if safe in EXCLUDE_PATHS or ch["title"] in EXCLUDE_PATHS:
                continue
            out[ch["token"]] = "/".join(parts + [safe + ".docx"])
            assign(ch["token"], parts + [safe])

    assign(None, [])
    return out


# --------------------------------------------------------------------- content
def get_node_info(token, space_id, auth_token, cache):
    if token in cache:
        return cache[token]
    url = f"{HOST}/open-apis/wiki/v2/spaces/{space_id}/nodes/{token}"
    s, c = call(url, token=auth_token)
    if s == 200 and isinstance(c, dict) and c.get("code") == 0:
        node = c.get("data", {}).get("node", {})
        res = (node.get("obj_token", ""), node.get("obj_type", ""),
               node.get("last_edit_time", 0) or 0)
    else:
        res = (None, None, 0)
    cache[token] = res
    return res


def download_docx(token, obj_token, out_path):
    s, c = call(f"{HOST}/open-apis/drive/v1/export_tasks", token=token, method="POST",
                data={"file_extension": "docx", "token": obj_token, "type": "docx"})
    if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
        return False, f"export task failed: {s} {c}"
    d = c.get("data", {})
    ticket = d.get("ticket") or d.get("task_id") or d.get("id")
    if not ticket:
        return False, f"no ticket: {c}"
    for attempt in range(6):
        time.sleep(2)
        s2, c2 = call(f"{HOST}/open-apis/drive/v1/export_tasks/{ticket}?token={obj_token}",
                      token=token)
        if s2 != 200 or not isinstance(c2, dict) or c2.get("code") != 0:
            continue
        result = c2.get("data", {}).get("result", c2.get("data", {}))
        if result.get("job_status") == 3:
            return False, f"export failed: {result.get('job_error_msg', '')}"
        file_token = result.get("file_token")
        if file_token:
            file_id = file_token.split("?")[0]
            req = urllib.request.Request(
                f"{HOST}/open-apis/drive/v1/medias/{file_id}/download",
                headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=60) as resp:
                blob = resp.read()
            if not blob:
                return False, "empty export"
            with open(out_path, "wb") as f:
                f.write(blob)
            return True, f"{len(blob)} bytes"
    return False, "export timeout"


# -------------------------------------------------------------------- manifest
def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_manifest(m):
    tmp = MANIFEST + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args],
                          capture_output=True, text=True, timeout=600)


# ------------------------------------------------------------------------ main
def main():
    if not SPACE_ID:
        sys.exit("set LARK_SPACE_ID")
    os.makedirs(ROOT, exist_ok=True)
    tok = get_token()

    if TREE_FILE and os.path.exists(TREE_FILE):
        print(f"node list: tree snapshot {TREE_FILE}")
        items = parse_tree_md(TREE_FILE)
    else:
        print("node list: live crawl")
        items = crawl_live(tok, SPACE_ID)

    if not items:
        sys.exit("crawl returned zero nodes — refusing to run (would trash everything). "
                 "Check the app is still a member of the space.")

    path_map = build_path_map(items)
    current = set(path_map)
    manifest = load_manifest()
    cache = {}
    new = updated = renamed = skipped = 0
    errors = []

    for i, it in enumerate(items, 1):
        node = it["token"]
        rel = path_map.get(node)
        if not rel:
            continue
        out_path = os.path.join(ROOT, rel)
        obj_token, obj_type, let = get_node_info(node, SPACE_ID, tok, cache)
        if not obj_token or obj_type not in NODE_TYPES:
            continue

        rec = manifest.get(node)
        if rec:
            if rec.get("last_edit_time") != let:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                ok, msg = download_docx(tok, obj_token, out_path)
                print(f"[{i}/{len(items)}] update {it['title']}: {'OK ' + msg if ok else 'FAIL ' + msg}")
                if ok:
                    updated += 1
                    rec.update(last_edit_time=let, path=rel, fetched=int(time.time()))
                else:
                    errors.append(node)
            elif rec.get("path") != rel:
                old = os.path.join(ROOT, rec["path"])
                if os.path.exists(old):
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    os.rename(old, out_path)
                    print(f"[{i}/{len(items)}] rename {rec['path']} -> {rel}")
                rec["path"] = rel
                renamed += 1
            else:
                skipped += 1
            manifest[node] = rec
            time.sleep(REQUEST_DELAY)
            continue

        if os.path.exists(out_path):
            manifest[node] = {"path": rel, "last_edit_time": let, "fetched": int(time.time())}
            skipped += 1
            continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        ok, msg = download_docx(tok, obj_token, out_path)
        print(f"[{i}/{len(items)}] new {it['title']}: {'OK ' + msg if ok else 'FAIL ' + msg}")
        if ok:
            new += 1
            manifest[node] = {"path": rel, "last_edit_time": let, "fetched": int(time.time())}
        else:
            errors.append(node)
        time.sleep(REQUEST_DELAY)

    deleted = [t for t in list(manifest) if t not in current]
    for t in deleted:
        p = manifest[t].get("path")
        if p and os.path.exists(os.path.join(ROOT, p)):
            dst = os.path.join(TRASH_DIR, p.replace("/", "__"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(os.path.join(ROOT, p), dst)
            print(f"[deleted] {p} -> .trash/")
        del manifest[t]
    if deleted:
        print(f"pruned {len(deleted)} upstream-deleted node(s)")

    save_manifest(manifest)

    if GIT_COMMIT and any((new, updated, renamed, deleted)) and os.path.isdir(os.path.join(ROOT, ".git")):
        git("add", "-A", "--", ".", ":(exclude).manifest.json", ":(exclude).trash")
        if git("diff", "--cached", "--quiet").returncode == 1:
            import datetime
            msg = (f"delta docx: +{new} ~{updated} renamed {renamed} "
                   f"pruned {len(deleted)} ({datetime.date.today().isoformat()})")
            r = git("commit", "-m", msg)
            print(f"[git] {'committed: ' + msg if r.returncode == 0 else 'commit failed: ' + r.stderr}")

    print(f"\ndone: new={new} updated={updated} renamed={renamed} "
          f"skipped={skipped} deleted={len(deleted)} errors={len(errors)}")
    if errors:
        print(f"failed nodes (first 10): {errors[:10]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
