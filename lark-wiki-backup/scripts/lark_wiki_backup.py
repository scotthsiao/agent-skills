#!/usr/bin/env python3
"""
lark_wiki_backup.py — incremental (delta) mirror of Lark/Feishu content to local
files, git-committed.

Two sources:
  LARK_SOURCE=wiki   (default)  a wiki space (知识库)      -> tenant_access_token
  LARK_SOURCE=drive             personal / shared Drive     -> user_access_token

Per node, keyed by token in .manifest.json:
  - not tracked, file absent          -> download
  - change key newer than manifest    -> re-download
  - change key same, path changed     -> os.rename locally (no re-fetch)
  - unchanged                          -> skip
  - tracked but not in this run's tree -> upstream-deleted -> move to .trash/

Standard library only.

Common env:
  BACKUP_ROOT         output dir, should be a git repo  (default: ./lark-wiki-backup-data)
  LARK_ENV_FILE       dotenv with credentials           (default: ~/.hermes/.env)
  LARK_DOMAIN         lark | feishu                      (default: lark)
  LARK_EXCLUDE_PATHS  comma-sep title segments to skip entirely
  LARK_GIT_COMMIT     1 (default) | 0
  LARK_SOURCE         wiki (default) | drive

wiki source:
  LARK_SPACE_ID       (required) wiki space id
  LARK_TREE_FILE      optional markdown tree snapshot for the node list
  LARK_NODE_TYPES     comma-sep obj_types to export      (default: docx)

drive source:
  LARK_USER_TOKEN     a user_access_token (expires ~2h)
  LARK_USER_TOKEN_CMD shell command that prints a fresh user_access_token
                      (use this for cron — e.g. wrap `lark-cli`, or a refresh script)
  LARK_DRIVE_FOLDER_TOKEN  a specific folder to mirror; omit = personal root ("My Space")
"""
import os
import re
import sys
import json
import time
import subprocess
import urllib.error
import urllib.request

SOURCE = os.environ.get("LARK_SOURCE", "wiki")
ROOT = os.path.abspath(os.environ.get("BACKUP_ROOT", "./lark-wiki-backup-data"))
ENV_FILE = os.path.expanduser(os.environ.get("LARK_ENV_FILE", "~/.hermes/.env"))
DOMAIN = os.environ.get("LARK_DOMAIN", "lark")
EXCLUDE_PATHS = {s.strip() for s in os.environ.get("LARK_EXCLUDE_PATHS", "").split(",") if s.strip()}
GIT_COMMIT = os.environ.get("LARK_GIT_COMMIT", "1") == "1"

SPACE_ID = os.environ.get("LARK_SPACE_ID", "")
TREE_FILE = os.environ.get("LARK_TREE_FILE", "")
NODE_TYPES = {s.strip() for s in os.environ.get("LARK_NODE_TYPES", "docx").split(",") if s.strip()}

DRIVE_FOLDER_TOKEN = os.environ.get("LARK_DRIVE_FOLDER_TOKEN", "")

HOST = "https://open.feishu.cn" if DOMAIN == "feishu" else "https://open.larksuite.com"
MANIFEST = os.path.join(ROOT, ".manifest.json")
TRASH_DIR = os.path.join(ROOT, ".trash")
REQUEST_DELAY = 0.15
BACKOFF_MAX = 120

# Drive native doc type -> (export file_extension, export `type` param)
DRIVE_EXPORT = {
    "docx": ("docx", "docx"),
    "doc": ("docx", "doc"),
    "sheet": ("xlsx", "sheet"),
    "bitable": ("xlsx", "bitable"),
    "mindnote": ("pdf", "mindnote"),
    "slides": ("pdf", "slides"),
}


# --------------------------------------------------------------------------- io
def _dotenv():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


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


def get_tenant_token():
    env = _dotenv()
    app_id = os.environ.get("FEISHU_APP_ID") or env.get("FEISHU_APP_ID")
    secret = os.environ.get("FEISHU_APP_SECRET") or env.get("FEISHU_APP_SECRET")
    if not app_id or not secret:
        sys.exit("wiki mode needs FEISHU_APP_ID / FEISHU_APP_SECRET (env or LARK_ENV_FILE)")
    s, c = call(f"{HOST}/open-apis/auth/v3/tenant_access_token/internal",
                method="POST", data={"app_id": app_id, "app_secret": secret})
    if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
        sys.exit(f"token request failed: {s} {c}")
    return c["tenant_access_token"]


def get_user_token():
    tok = os.environ.get("LARK_USER_TOKEN", "").strip()
    if not tok:
        cmd = os.environ.get("LARK_USER_TOKEN_CMD", "").strip()
        if cmd:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            tok = r.stdout.strip()
            if not tok:
                sys.exit(f"LARK_USER_TOKEN_CMD produced no token. stderr: {r.stderr[:300]}")
    if not tok:
        sys.exit("drive mode needs LARK_USER_TOKEN or LARK_USER_TOKEN_CMD "
                 "(a user_access_token — obtain via an OAuth flow, e.g. @larksuite/cli)")
    return tok


def get_auth():
    return get_user_token() if SOURCE == "drive" else get_tenant_token()


# ------------------------------------------------------------------ shared util
def safe_stem(title, max_len=80):
    title = re.sub(r'[\\/:*?"<>|]', "_", title).strip(". ")
    return title[:max_len].rstrip() or "untitled"


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args],
                          capture_output=True, text=True, timeout=600)


def export_download(token, obj_token, export_type, ext, out_path):
    """Export a Lark-native doc (docx/sheet/bitable/...) and download the result."""
    s, c = call(f"{HOST}/open-apis/drive/v1/export_tasks", token=token, method="POST",
                data={"file_extension": ext, "token": obj_token, "type": export_type})
    if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
        return False, f"export task failed: {s} {c}"
    d = c.get("data", {})
    ticket = d.get("ticket") or d.get("task_id") or d.get("id")
    if not ticket:
        return False, f"no ticket: {c}"
    for _ in range(6):
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
            with urllib.request.urlopen(req, timeout=90) as resp:
                blob = resp.read()
            if not blob:
                return False, "empty export"
            with open(out_path, "wb") as f:
                f.write(blob)
            return True, f"{len(blob)} bytes"
    return False, "export timeout"


def raw_download(token, file_token, out_path):
    """Download an uploaded (non-native) Drive file as-is."""
    req = urllib.request.Request(
        f"{HOST}/open-apis/drive/v1/files/{file_token}/download",
        headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:200]!r}"
    if not blob:
        return False, "empty file"
    with open(out_path, "wb") as f:
        f.write(blob)
    return True, f"{len(blob)} bytes"


# --------------------------------------------------------------- WIKI adapter
def wiki_crawl_live(token):
    items = []

    def children(parent_token):
        out, page = [], None
        while True:
            url = (f"{HOST}/open-apis/wiki/v2/spaces/{SPACE_ID}/nodes"
                   f"?page_size=50&parent_node_token={parent_token}"
                   + (f"&page_token={page}" if page else ""))
            s, c = call(url, token=token)
            if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
                print(f"[crawl] list failed at {parent_token or 'root'}: {s} {c}", file=sys.stderr)
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


def wiki_parse_tree_md(path):
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


def wiki_list(token):
    if TREE_FILE and os.path.exists(TREE_FILE):
        print(f"node list: tree snapshot {TREE_FILE}")
        return wiki_parse_tree_md(TREE_FILE)
    print("node list: live crawl (wiki space)")
    return wiki_crawl_live(token)


def wiki_enrich(item, token, cache):
    tk = item["token"]
    if tk not in cache:
        s, c = call(f"{HOST}/open-apis/wiki/v2/spaces/{SPACE_ID}/nodes/{tk}", token=token)
        if s == 200 and isinstance(c, dict) and c.get("code") == 0:
            node = c.get("data", {}).get("node", {})
            cache[tk] = (node.get("obj_token", ""), node.get("obj_type", ""),
                         node.get("last_edit_time", 0) or 0)
        else:
            cache[tk] = (None, None, 0)
    obj_token, obj_type, let = cache[tk]
    if not obj_token or obj_type not in NODE_TYPES:
        return None
    return {"kind": "export", "content_token": obj_token, "export_type": obj_type,
            "ext": "docx", "change_key": let}


# --------------------------------------------------------------- DRIVE adapter
def drive_root(token):
    s, c = call(f"{HOST}/open-apis/drive/explorer/v2/root_folder/meta", token=token)
    if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
        sys.exit(f"could not read personal root folder: {s} {c} "
                 "(token needs the drive read scope; or set LARK_DRIVE_FOLDER_TOKEN)")
    return c["data"]["token"]


def drive_children(token, folder_token):
    out, page = [], None
    while True:
        url = (f"{HOST}/open-apis/drive/v1/files?folder_token={folder_token}"
               f"&page_size=200&order_by=EditedTime&direction=DESC"
               + (f"&page_token={page}" if page else ""))
        s, c = call(url, token=token)
        if s != 200 or not isinstance(c, dict) or c.get("code") != 0:
            print(f"[drive] list failed in {folder_token}: {s} {c}", file=sys.stderr)
            return out
        data = c.get("data", {})
        out.extend(data.get("files", []))
        if not data.get("has_more"):
            return out
        page = data.get("next_page_token")
        time.sleep(0.2)


def drive_list(token):
    root = DRIVE_FOLDER_TOKEN or drive_root(token)
    print(f"node list: Drive walk from {root}"
          + ("" if DRIVE_FOLDER_TOKEN else " (personal root / My Space)"))
    items = []

    def walk(folder_token, depth, parent):
        for f in drive_children(token, folder_token):
            if f.get("shortcut_info"):        # don't follow shortcuts
                continue
            items.append({
                "token": f["token"], "title": f.get("name", "untitled"),
                "depth": depth, "parent": parent,
                "dtype": f.get("type", ""), "modified": str(f.get("modified_time", "0")),
            })
            if f.get("type") == "folder":
                walk(f["token"], depth + 1, f["token"])
                time.sleep(0.15)

    walk(root, 0, None)
    return items


def drive_enrich(item, token, cache):  # noqa: ARG001 - signature parity
    dt = item.get("dtype", "")
    if dt == "folder":
        return None
    if dt == "file":
        return {"kind": "download", "content_token": item["token"], "ext": None,
                "change_key": item["modified"]}
    if dt in DRIVE_EXPORT:
        ext, etype = DRIVE_EXPORT[dt]
        return {"kind": "export", "content_token": item["token"], "export_type": etype,
                "ext": ext, "change_key": item["modified"]}
    print(f"[drive] skip unsupported type '{dt}': {item['title']}", file=sys.stderr)
    return None


ADAPTERS = {
    "wiki": (wiki_list, wiki_enrich),
    "drive": (drive_list, drive_enrich),
}


# -------------------------------------------------------------------- pathing
def build_path_map(items, meta):
    kids = {}
    for it in items:
        kids.setdefault(it.get("parent"), []).append(it)
    out = {}

    def assign(parent, parts):
        for ch in kids.get(parent, []):
            base = safe_stem(ch["title"], 60)
            if base in EXCLUDE_PATHS or ch["title"] in EXCLUDE_PATHS:
                continue
            m = meta.get(ch["token"])
            if m:
                if m["ext"]:
                    fname = f"{base}.{m['ext']}"
                else:  # raw Drive file: keep its own extension
                    fname = base + os.path.splitext(ch["title"])[1]
                out[ch["token"]] = "/".join(parts + [fname])
            assign(ch["token"], parts + [base])

    assign(None, [])
    return out


# -------------------------------------------------------------------- manifest
def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            m = json.load(open(MANIFEST, encoding="utf-8"))
            for rec in m.values():                       # migrate old key name
                if "change_key" not in rec and "last_edit_time" in rec:
                    rec["change_key"] = rec["last_edit_time"]
            return m
        except Exception:
            pass
    return {}


def save_manifest(m):
    tmp = MANIFEST + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


# ------------------------------------------------------------------------ main
def main():
    if SOURCE not in ADAPTERS:
        sys.exit(f"LARK_SOURCE must be one of {list(ADAPTERS)}")
    if SOURCE == "wiki" and not SPACE_ID:
        sys.exit("wiki mode: set LARK_SPACE_ID")

    os.makedirs(ROOT, exist_ok=True)
    list_fn, enrich_fn = ADAPTERS[SOURCE]
    tok = get_auth()

    items = list_fn(tok)
    if not items:
        sys.exit("listing returned zero nodes — refusing to run (would trash everything). "
                 "Check access to the space/folder.")

    cache = {}
    meta = {}
    for it in items:
        m = enrich_fn(it, tok, cache)
        if m:
            meta[it["token"]] = m

    path_map = build_path_map(items, meta)
    current = set(path_map)
    manifest = load_manifest()
    new = updated = renamed = skipped = 0
    errors = []

    for i, it in enumerate(items, 1):
        node = it["token"]
        rel = path_map.get(node)
        m = meta.get(node)
        if not rel or not m:
            continue
        out_path = os.path.join(ROOT, rel)

        def fetch():
            if m["kind"] == "download":
                return raw_download(tok, m["content_token"], out_path)
            return export_download(tok, m["content_token"], m["export_type"], m["ext"], out_path)

        rec = manifest.get(node)
        if rec:
            if str(rec.get("change_key")) != str(m["change_key"]):
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                ok, msg = fetch()
                print(f"[{i}/{len(items)}] update {it['title']}: {'OK ' + msg if ok else 'FAIL ' + msg}")
                if ok:
                    updated += 1
                    rec.update(change_key=m["change_key"], path=rel, fetched=int(time.time()))
                    rec.pop("last_edit_time", None)
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
            manifest[node] = {"path": rel, "change_key": m["change_key"], "fetched": int(time.time())}
            skipped += 1
            continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        ok, msg = fetch()
        print(f"[{i}/{len(items)}] new {it['title']}: {'OK ' + msg if ok else 'FAIL ' + msg}")
        if ok:
            new += 1
            manifest[node] = {"path": rel, "change_key": m["change_key"], "fetched": int(time.time())}
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
            msg = (f"delta {SOURCE}: +{new} ~{updated} renamed {renamed} "
                   f"pruned {len(deleted)} ({datetime.date.today().isoformat()})")
            r = git("commit", "-m", msg)
            print(f"[git] {'committed: ' + msg if r.returncode == 0 else 'commit failed: ' + r.stderr}")

    print(f"\ndone ({SOURCE}): new={new} updated={updated} renamed={renamed} "
          f"skipped={skipped} deleted={len(deleted)} errors={len(errors)}")
    if errors:
        print(f"failed nodes (first 10): {errors[:10]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
