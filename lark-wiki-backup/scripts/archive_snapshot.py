#!/usr/bin/env python3
"""
archive_snapshot.py — tar a local directory (e.g. a lark_wiki_backup mirror) and
push it offsite. Stage 2 of lark-wiki-backup; also usable standalone.

  ARCHIVE_SRC=~/lark-wiki/team ARCHIVE_NAME=teamwiki \
  ARCHIVE_UPLOAD=rclone RCLONE_REMOTE=gdrive RCLONE_REMOTE_DIR="Lark Wiki Backups" \
  python3 archive_snapshot.py

Env:
  ARCHIVE_SRC          (required) directory to archive
  ARCHIVE_NAME         archive basename prefix          (default: snapshot)
  ARCHIVE_OUT          local dir for archives           (default: ~/backups)
  ARCHIVE_UPLOAD       rclone | gdrive-api | none       (default: none)
  ARCHIVE_KEEP_LOCAL   local archives to retain         (default: 7)
  ARCHIVE_EXCLUDE_EXT  comma-sep extensions to omit     (default: video formats)
  ARCHIVE_EXCLUDE_DIRS comma-sep dir names to omit      (default: .git,.trash,__pycache__)
  RCLONE_REMOTE / RCLONE_REMOTE_DIR                       for ARCHIVE_UPLOAD=rclone
  GDRIVE_TOKEN_FILE / GDRIVE_FOLDER_NAME                  for ARCHIVE_UPLOAD=gdrive-api
  ARCHIVE_NOTIFY_CMD   shell cmd; one-line summary on stdin (NUL-terminated)
"""
import os
import sys
import glob
import time
import shutil
import tarfile
import tempfile
import subprocess
import datetime as dt

SRC = os.path.expanduser(os.environ.get("ARCHIVE_SRC", ""))
NAME = os.environ.get("ARCHIVE_NAME", "snapshot")
OUT = os.path.expanduser(os.environ.get("ARCHIVE_OUT", "~/backups"))
UPLOAD = os.environ.get("ARCHIVE_UPLOAD", "none")
KEEP = int(os.environ.get("ARCHIVE_KEEP_LOCAL", "7"))
EXCLUDE_EXT = {e.strip().lower() for e in os.environ.get(
    "ARCHIVE_EXCLUDE_EXT",
    ".mp4,.mov,.avi,.mkv,.webm,.flv,.wmv,.m4v,.mpg,.mpeg,.3gp").split(",") if e.strip()}
EXCLUDE_DIRS = {d.strip() for d in os.environ.get(
    "ARCHIVE_EXCLUDE_DIRS", ".git,.trash,__pycache__").split(",") if d.strip()}
EXCLUDE_NAMES = {".manifest.json"}
NOTIFY_CMD = os.environ.get("ARCHIVE_NOTIFY_CMD", "")
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
RCLONE_REMOTE_DIR = os.environ.get("RCLONE_REMOTE_DIR", "Snapshots")
GDRIVE_TOKEN_FILE = os.path.expanduser(
    os.environ.get("GDRIVE_TOKEN_FILE", "~/.config/app/token.json"))
GDRIVE_FOLDER_NAME = os.environ.get("GDRIVE_FOLDER_NAME", RCLONE_REMOTE_DIR)


def notify(line):
    print(line)
    if NOTIFY_CMD:
        try:
            subprocess.run(NOTIFY_CMD, shell=True, input=(line + "\0").encode(),
                           timeout=30, check=False)
        except Exception as e:  # noqa: BLE001
            print(f"(notify failed: {e})", file=sys.stderr)


def collect():
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if fn in EXCLUDE_NAMES:
                continue
            if os.path.splitext(fn)[1].lower() in EXCLUDE_EXT:
                continue
            yield os.path.join(root, fn)


def upload_rclone(path):
    r = subprocess.run(["rclone", "copy", path, f"{RCLONE_REMOTE}:{RCLONE_REMOTE_DIR}/", "-v"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode == 0


def upload_gdrive_api(path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    scopes = ["https://www.googleapis.com/auth/drive.file"]
    creds = Credentials.from_authorized_user_file(GDRIVE_TOKEN_FILE, scopes)
    if not creds.valid:
        creds.refresh(Request())
    svc = build("drive", "v3", credentials=creds)
    q = (f"name='{GDRIVE_FOLDER_NAME}' and "
         "mimeType='application/vnd.google-apps.folder' and trashed=false")
    hits = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    fid = (hits[0]["id"] if hits else svc.files().create(
        body={"name": GDRIVE_FOLDER_NAME,
              "mimeType": "application/vnd.google-apps.folder"},
        fields="id").execute()["id"])
    svc.files().create(body={"name": os.path.basename(path), "parents": [fid]},
                       media_body=MediaFileUpload(path, resumable=True),
                       fields="id").execute()
    return True


def main():
    if not SRC or not os.path.isdir(SRC):
        sys.exit("set ARCHIVE_SRC to an existing directory")
    os.makedirs(OUT, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = tempfile.mkdtemp(prefix="larkbak_")
    arc = os.path.join(tmp, f"{NAME}_{stamp}.tar.gz")

    files = list(collect())
    if not files:
        notify(f"archive [{NAME}] nothing to archive under {SRC}")
        return 0
    total = sum(os.path.getsize(f) for f in files)
    with tarfile.open(arc, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=os.path.join(NAME, os.path.relpath(f, SRC)))
    size_mb = os.path.getsize(arc) / 1024 / 1024
    local = shutil.copy2(arc, OUT)
    shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    if UPLOAD == "rclone":
        ok = upload_rclone(local)
    elif UPLOAD == "gdrive-api":
        try:
            ok = upload_gdrive_api(local)
        except Exception as e:  # noqa: BLE001
            print(f"[gdrive-api] {e}", file=sys.stderr)
            ok = False

    for old in sorted(glob.glob(os.path.join(OUT, f"{NAME}_*.tar.gz")),
                      key=os.path.getmtime)[:-KEEP]:
        os.remove(old)
        print(f"pruned {os.path.basename(old)}")

    tag = {"rclone": f"{RCLONE_REMOTE}:{RCLONE_REMOTE_DIR}",
           "gdrive-api": GDRIVE_FOLDER_NAME}.get(UPLOAD, "local only")
    if ok:
        notify(f"archive OK [{NAME}] {len(files)} files, "
               f"{total / 1024 / 1024:.1f} MB -> {size_mb:.1f} MB -> {tag}")
        return 0
    notify(f"archive PARTIAL [{NAME}] local kept, upload FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
