#!/usr/bin/env python3
"""
Config backup for a long-running headless app.

  select (config/auth/state/content, never caches)
    -> archive (tar.gz)
    -> optionally encrypt (age)
    -> upload (rclone | gdrive-api | none)
    -> prune local copies
    -> notify

Usage:
  python3 config_backup.py daily     # minimal, fast set
  python3 config_backup.py weekly    # fuller home sweep with heavy dirs pruned

Configuration: env vars (below) or a `backup.toml` beside this script with a
[backup] table using the same lowercased keys without the BACKUP_ prefix.

Env:
  BACKUP_HOME            default: ~/.hermes
  BACKUP_OUT             default: ~/backups
  BACKUP_DAILY_INCLUDE   comma-sep paths relative to HOME (default below)
  BACKUP_PRUNE_DIRS      comma-sep dir names never traversed (default below)
  BACKUP_KEEP            default: 7   (local archives kept per tier)
  BACKUP_UPLOAD          rclone | gdrive-api | none   (default: rclone)
  RCLONE_REMOTE          default: gdrive
  RCLONE_REMOTE_DIR      default: AppBackups
  GDRIVE_TOKEN_FILE      default: ~/.config/app/token.json
  GDRIVE_FOLDER_NAME     default: AppBackups
  BACKUP_ENCRYPT_TO      optional: an `age` recipient; if set, archive is
                         `age`-encrypted before upload (needs the `age` binary)
  BACKUP_NOTIFY_CMD      optional: shell cmd, gets a one-line summary on stdin
                         (NUL-terminated; use `xargs -0`)
  BACKUP_VENV_PYTHON     optional: re-exec under this interpreter (for gdrive-api
                         deps that live in the app venv)
"""
import os
import sys
import glob
import time
import json
import shutil
import tarfile
import tempfile
import subprocess
import datetime as dt

HOME = os.path.expanduser(os.environ.get("BACKUP_HOME", "~/.hermes"))
OUT = os.path.expanduser(os.environ.get("BACKUP_OUT", "~/backups"))
KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
UPLOAD = os.environ.get("BACKUP_UPLOAD", "rclone")
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
RCLONE_REMOTE_DIR = os.environ.get("RCLONE_REMOTE_DIR", "AppBackups")
GDRIVE_TOKEN_FILE = os.path.expanduser(
    os.environ.get("GDRIVE_TOKEN_FILE", "~/.config/app/token.json"))
GDRIVE_FOLDER_NAME = os.environ.get("GDRIVE_FOLDER_NAME", "AppBackups")
ENCRYPT_TO = os.environ.get("BACKUP_ENCRYPT_TO", "")
NOTIFY_CMD = os.environ.get("BACKUP_NOTIFY_CMD", "")
VENV_PYTHON = os.environ.get("BACKUP_VENV_PYTHON", "")

DEFAULT_DAILY_INCLUDE = [
    "config.yaml", "config.toml", "settings.json", ".env",
    "SOUL.md", "AGENTS.md", "auth.json",
    "state.db", "kanban.db", "tasks.db",
    "channel_directory.json", "gateway_state.json", "processes.json",
    "cron", "sessions", "memories", "skills", "scripts", "hooks", "profiles",
]
DEFAULT_PRUNE_DIRS = {
    "node_modules", "node", "cache", "bootstrap-cache", "audio_cache",
    "image_cache", "paste-cache", "__pycache__", "logs", "backups",
    ".git", "venv", ".venv", "models",
}

DAILY_INCLUDE = [s.strip() for s in os.environ.get(
    "BACKUP_DAILY_INCLUDE", ",".join(DEFAULT_DAILY_INCLUDE)).split(",") if s.strip()]
PRUNE_DIRS = {s.strip() for s in os.environ.get(
    "BACKUP_PRUNE_DIRS", ",".join(sorted(DEFAULT_PRUNE_DIRS))).split(",") if s.strip()}


def _maybe_reexec():
    if VENV_PYTHON and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        if os.path.exists(VENV_PYTHON):
            os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])


def _load_toml_overrides():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup.toml")
    if not os.path.exists(path):
        return
    try:
        import tomllib
        with open(path, "rb") as f:
            table = tomllib.load(f).get("backup", {})
        for k, v in table.items():
            os.environ.setdefault("BACKUP_" + k.upper(), str(v))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not read backup.toml: {e}", file=sys.stderr)


def _stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def build_archive(tier: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="cfgbak_")
    arc = os.path.join(tmp, f"{tier}-{_stamp()}.tar.gz")

    with tarfile.open(arc, "w:gz") as tar:
        if tier == "daily":
            for rel in DAILY_INCLUDE:
                p = os.path.join(HOME, rel)
                if os.path.exists(p):
                    tar.add(p, arcname=rel, filter=_skip_sidecars)
        else:  # weekly: whole home, pruned
            for root, dirs, files in os.walk(HOME):
                dirs[:] = [d for d in dirs if d not in PRUNE_DIRS]
                for fn in files:
                    fp = os.path.join(root, fn)
                    if fn.endswith((".db-wal", ".db-shm")):
                        continue
                    try:
                        tar.add(fp, arcname=os.path.relpath(fp, HOME))
                    except (OSError, PermissionError):
                        pass
    return arc


def _skip_sidecars(info: "tarfile.TarInfo"):
    if info.name.endswith((".db-wal", ".db-shm", ".lock")):
        return None
    return info


def maybe_encrypt(path: str) -> str:
    if not ENCRYPT_TO:
        return path
    enc = path + ".age"
    subprocess.run(["age", "-r", ENCRYPT_TO, "-o", enc, path], check=True)
    os.remove(path)
    return enc


def upload_rclone(path: str) -> bool:
    dest = f"{RCLONE_REMOTE}:{RCLONE_REMOTE_DIR}/"
    r = subprocess.run(["rclone", "copy", path, dest, "-v"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode == 0


def upload_gdrive_api(path: str) -> bool:
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        print(f"[gdrive-api] missing deps: {e}", file=sys.stderr)
        return False
    scopes = ["https://www.googleapis.com/auth/drive.file"]
    creds = Credentials.from_authorized_user_file(GDRIVE_TOKEN_FILE, scopes)
    if not creds.valid:
        creds.refresh(Request())
        json.dump(json.loads(creds.to_json()), open(GDRIVE_TOKEN_FILE, "w"), indent=2)
    svc = build("drive", "v3", credentials=creds)
    q = (f"name='{GDRIVE_FOLDER_NAME}' and "
         "mimeType='application/vnd.google-apps.folder' and trashed=false")
    found = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    folder_id = (found[0]["id"] if found else
                 svc.files().create(body={"name": GDRIVE_FOLDER_NAME,
                                          "mimeType": "application/vnd.google-apps.folder"},
                                    fields="id").execute()["id"])
    svc.files().create(
        body={"name": os.path.basename(path), "parents": [folder_id]},
        media_body=MediaFileUpload(path, resumable=True), fields="id").execute()
    return True


def prune(tier: str):
    archives = sorted(
        glob.glob(os.path.join(OUT, f"{tier}-*")), key=os.path.getmtime)
    for old in archives[:-KEEP]:
        os.remove(old)
        print(f"pruned {os.path.basename(old)}")


def notify(line: str):
    print(line)
    if NOTIFY_CMD:
        try:
            subprocess.run(NOTIFY_CMD, shell=True,
                           input=(line + "\0").encode(), timeout=30, check=False)
        except Exception as e:  # noqa: BLE001
            print(f"(notify failed: {e})", file=sys.stderr)


def main() -> int:
    _maybe_reexec()
    _load_toml_overrides()
    tier = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if tier not in ("daily", "weekly"):
        sys.exit("usage: config_backup.py [daily|weekly]")

    try:
        arc = build_archive(tier)
        arc = maybe_encrypt(arc)
        size_mb = os.path.getsize(arc) / 1024 / 1024
        local = shutil.copy2(arc, OUT)
        shutil.rmtree(os.path.dirname(arc), ignore_errors=True)

        uploaded = True
        if UPLOAD == "rclone":
            uploaded = upload_rclone(local)
        elif UPLOAD == "gdrive-api":
            uploaded = upload_gdrive_api(local)

        prune(tier)
        where = {"rclone": f"{RCLONE_REMOTE}:{RCLONE_REMOTE_DIR}",
                 "gdrive-api": GDRIVE_FOLDER_NAME, "none": "local only"}.get(UPLOAD, UPLOAD)
        if uploaded:
            notify(f"backup OK [{tier}] {os.path.basename(local)} "
                   f"({size_mb:.1f} MB) -> {where}")
            return 0
        notify(f"backup PARTIAL [{tier}] {os.path.basename(local)} "
               f"({size_mb:.1f} MB) — local kept, upload FAILED")
        return 1
    except Exception as e:  # noqa: BLE001
        notify(f"backup FAILED [{tier}] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
