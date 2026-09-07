---
name: lark-wiki-backup
description: >-
  Snapshot a Lark / Feishu (飞书) wiki space OR a personal/shared Drive folder to
  versioned local files — incrementally, keeping only what changed — and optionally
  push a compressed archive offsite. Use when asked to "back up the Lark knowledge
  base / wiki space / my Lark cloud files", to mirror Lark content locally, to keep
  an offline or git-tracked copy of team or personal docs, to guard against someone
  deleting a Lark page, or to feed Lark content into a local search / RAG pipeline.
version: 1.1.0
license: MIT
metadata:
  tags: [lark, feishu, wiki, drive, backup, snapshot, delta, incremental, git, docx, disaster-recovery]
  related_skills: [lark-openapi-recipes, headless-config-backup, no-agent-cron-scripts, cron-timezone-discipline]
---

# Lark / Feishu wiki & Drive backup

Keep a **local, versioned mirror** of Lark/Feishu content. Each run pulls only the
nodes that were added or edited since last time, renames files in place when a page
is retitled, moves upstream-deleted pages to a `.trash/` folder, and commits the
result to git. An optional second step tars the mirror and uploads it offsite.

## Two sources

| `LARK_SOURCE` | mirrors | auth | notes |
|---|---|---|---|
| `wiki` (default) | a **wiki space** (知识库) | `tenant_access_token` — an app that is a member of the space | docx nodes |
| `drive` | a **personal ("My Space") or shared Drive folder** | **`user_access_token`** — OAuth as the user; a bot token cannot see anyone's Drive | docx / sheet / bitable / mindnote exported, uploaded files copied as-is |

The delta engine, manifest, rename/trash handling, git commit, and the offsite
archive step are **identical** for both — only the "list what's there" and "how to
authenticate" differ.

Two stages, run in order (both are [`no-agent`](../no-agent-cron-scripts/) cron jobs):

| Stage | Script | Does |
|---|---|---|
| 1. mirror | `scripts/lark_wiki_backup.py` | Lark space → `.docx` files on disk, delta only, git commit |
| 2. archive | `scripts/archive_snapshot.py` | the mirror → `.tar.gz` → rclone / Google Drive |

Stage 1 is the real backup (versioned, restorable per-file). Stage 2 is the offsite
belt-and-braces.

## Prerequisites

Common: `git` on the box (the mirror dir should be a git repo), Python 3.9+, standard
library only.

**wiki source:**
- A Lark/Feishu **app** (App ID + Secret) that is a **member of the target wiki
  space** — otherwise node listing returns nothing (a scope wall, not an auth error;
  see [`lark-openapi-recipes`](../lark-openapi-recipes/)).
- App scopes: `wiki:wiki:readonly`, `docx:document:readonly`, `drive:export`. Publish
  a new app version after granting.

**drive source:**
- A **`user_access_token`** for the account whose Drive you're backing up. A bot /
  tenant token can't see personal Drive. Get one via an OAuth flow — the easiest is
  [`@larksuite/cli`](../lark-openapi-recipes/) with `--identity user-default`
  (`lark-cli auth login`), which also handles refresh.
- Scopes on that token: `drive:drive:readonly` (list + download), `docx:document:readonly`,
  `sheets:spreadsheet:readonly`, `drive:export`.
- **User tokens expire (~2h).** For a cron job you must supply a *command that prints
  a fresh one* via `LARK_USER_TOKEN_CMD` (see below), not a static `LARK_USER_TOKEN`.

## Stage 1 — mirror

### wiki space

```bash
export LARK_SPACE_ID=7xxxxxxxxxxxxxxxxxx        # the wiki space id
export LARK_ENV_FILE=~/.hermes/.env             # dotenv holding FEISHU_APP_ID / FEISHU_APP_SECRET
export BACKUP_ROOT=~/lark-wiki/space-name       # where the tree is written (a git repo)
python3 scripts/lark_wiki_backup.py
```

Find `LARK_SPACE_ID`: `lark-cli wiki +space-list --as user`, or open any page in the
space and call `wiki/v2/spaces/get_node?token=<page token>` — the response has
`space_id`.

### personal / shared Drive

```bash
export LARK_SOURCE=drive
export BACKUP_ROOT=~/lark-drive/my-space         # a git repo
# a command that prints a CURRENT user_access_token (refreshed as needed):
export LARK_USER_TOKEN_CMD='my-lark-token'       # see "getting a user token" below
# optional: a specific folder instead of the whole personal root:
# export LARK_DRIVE_FOLDER_TOKEN=fldxxxxxxxx
python3 scripts/lark_wiki_backup.py
```

With no `LARK_DRIVE_FOLDER_TOKEN`, it starts from your personal root ("My Space",
via `drive/explorer/v2/root_folder/meta`) and walks every sub-folder. To mirror a
**shared** folder or Space, pass its `folder_token` (from the folder's URL:
`.../drive/folder/<token>`).

Per Drive item:

| Drive `type` | handled as |
|---|---|
| `folder` | recurse |
| `docx`, `doc` | export → `.docx` |
| `sheet`, `bitable` | export → `.xlsx` |
| `mindnote`, `slides` | export → `.pdf` |
| `file` (uploaded pdf/img/zip/…) | downloaded **as-is**, original name kept |
| shortcut | skipped (not followed) |
| anything else | skipped with a warning |

### getting a user token for cron

The token must be fresh at run time. Options for `LARK_USER_TOKEN_CMD`:

- **Wrap `@larksuite/cli`** — after `lark-cli auth login --identity user-default`
  once, the CLI refreshes its own token; a tiny wrapper that prints the current
  access token (check `lark-cli auth --help` for a token/print subcommand on your
  version) is the least-effort path.
- **A refresh script** — store the `refresh_token` from your OAuth once, and have the
  command do `POST /open-apis/authen/v1/oidc/refresh_access_token` and print
  `data.access_token`. ~15 lines; same shape as
  [`gdrive-token-refresh`](../gdrive-token-refresh/)'s check script.

For a one-off manual run you can instead `export LARK_USER_TOKEN=<paste>` directly.

What it writes under `BACKUP_ROOT`:

```
<title path mirroring the tree>.<ext>          # one file per exportable node
.manifest.json                                 # {token: {path, change_key, fetched}}  (git-ignored)
.trash/                                        # nodes deleted upstream, kept for recovery (git-ignored)
```

The delta logic, per node (the **change key** is `last_edit_time` for wiki,
`modified_time` for drive):

| condition | action |
|---|---|
| token not in manifest, file absent | download |
| change key newer than manifest | re-download |
| change key same, title (path) changed | `os.rename` the local file — **no re-fetch** |
| unchanged | skip |
| in manifest but not in this run's tree | upstream-deleted → move to `.trash/`, drop from manifest |

First run (no manifest) downloads everything missing — same as a plain initial
export.

After a run with any change, it `git add` + `git commit` with a message like
`delta drive: +3 ~5 renamed 1 pruned 0 (2026-09-07)`.

### Options (env)

| var | applies to | default | meaning |
|---|---|---|---|
| `LARK_SOURCE` | both | `wiki` | `wiki` or `drive` |
| `BACKUP_ROOT` | both | `./lark-wiki-backup-data` | output dir (make it a git repo) |
| `LARK_DOMAIN` | both | `lark` | `lark` (larksuite.com) or `feishu` (feishu.cn) |
| `LARK_EXCLUDE_PATHS` | both | *(none)* | comma-sep title segments to skip entirely (e.g. a huge transient branch) |
| `LARK_GIT_COMMIT` | both | `1` | set `0` to skip the auto-commit |
| `LARK_SPACE_ID` | wiki | *(required)* | wiki space id |
| `LARK_ENV_FILE` | wiki | `~/.hermes/.env` | dotenv with `FEISHU_APP_ID` / `FEISHU_APP_SECRET` |
| `LARK_TREE_FILE` | wiki | *(none)* | optional markdown tree snapshot to read the node list from instead of a live crawl — faster for very large spaces |
| `LARK_NODE_TYPES` | wiki | `docx` | wiki node `obj_type`s to export |
| `LARK_USER_TOKEN` | drive | *(none)* | a user_access_token (one-off runs only — it expires) |
| `LARK_USER_TOKEN_CMD` | drive | *(none)* | shell command that prints a fresh user_access_token (use for cron) |
| `LARK_DRIVE_FOLDER_TOKEN` | drive | *(none)* | a specific folder to mirror; omit = personal root |

## Stage 2 — offsite archive

```bash
export ARCHIVE_SRC=~/lark-wiki/space-name
export ARCHIVE_NAME=teamwiki
export ARCHIVE_UPLOAD=rclone           # rclone | gdrive-api | none
export RCLONE_REMOTE=gdrive
export RCLONE_REMOTE_DIR="Lark Wiki Backups"
python3 scripts/archive_snapshot.py
```

Tars `ARCHIVE_SRC` (excluding `.git`, `.manifest.json`, `.trash/`, and any
`ARCHIVE_EXCLUDE_EXT` — video files by default), uploads, keeps `ARCHIVE_KEEP_LOCAL`
recent copies. The upload paths are the same as
[`headless-config-backup`](../headless-config-backup/) — if you already run that,
reuse its rclone remote and its [`gdrive-token-refresh`](../gdrive-token-refresh/)
health check.

## Cron wiring

```
# times shown local — convert to UTC (see cron-timezone-discipline)
mirror   daily 05:15   ->  lark_wiki_backup.py
archive  daily 23:00   ->  archive_snapshot.py
```

Run the mirror well before the archive so the archive picks up the day's delta.
Both are `no_agent` jobs — deliver stdout, don't wake the model.

## Restore

- **One page/file:** copy it back from the mirror (or from `.trash/` if it was
  deleted upstream), re-upload to Lark manually.
- **A branch or everything:** the git history is the source of truth — `git log`,
  `git checkout <sha> -- <path>`. The `.tar.gz` archives are the offsite fallback if
  the mirror box is lost.
- Lark has no bulk import API — restoring *into* Lark is manual per document. The
  value here is (a) you still have the content and its history, (b) you can diff what
  changed, (c) you can feed it to local tooling.

## Gotchas

- **Rate limits.** The export API throttles hard (error `99991400`). The script backs
  off and retries; a first run over a big space/Drive can take a while — let it.
- **The change key is free** — `last_edit_time` (wiki) is in the node metadata
  response, `modified_time` (drive) is in the folder listing — so a no-change run is
  cheap metadata calls, no content fetches.
- **Renames and tree moves are cheap** — local `os.rename` driven by the manifest.
  Only a real content edit re-downloads. (Drive note: `modified_time` *does* update on
  some metadata-only changes, so Drive re-downloads a bit more eagerly than wiki.)
- **`.manifest.json` and `.trash/` must be git-ignored** — the script assumes this;
  add them to the mirror repo's `.gitignore`.
- **Export can return an empty file** for an empty doc — the script skips those
  rather than writing a 0-byte file.
- **Don't lose access.** If the app is removed from the wiki space, or the drive
  user token loses scope, the next run sees an empty tree and *would* `.trash/`
  everything. The script **refuses to run when the listing returns zero nodes** — but
  watch for a sudden large "pruned N" in the output.
- **drive: user tokens expire (~2h).** A static `LARK_USER_TOKEN` works for one
  manual run; cron needs `LARK_USER_TOKEN_CMD` to fetch a fresh one each time.
- **drive: this adapter is newer and less battle-tested than the wiki one.** Do a
  first run against a small folder (`LARK_DRIVE_FOLDER_TOKEN`) and eyeball the result
  before pointing it at your whole Drive.

See [`references/delta-design.md`](references/delta-design.md) for the manifest
schema, the source-adapter split, and why it's built this way.
