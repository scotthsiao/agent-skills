---
name: lark-wiki-backup
description: >-
  Snapshot a Lark / Feishu (飞书) wiki space to versioned local files — incrementally,
  keeping only what changed — and optionally push a compressed archive offsite. Use
  when asked to "back up the Lark knowledge base / wiki space", to mirror a Lark
  space locally, to keep an offline or git-tracked copy of team docs, to guard
  against someone deleting a Lark page, or to feed Lark content into a local search /
  RAG pipeline.
version: 1.0.0
license: MIT
metadata:
  tags: [lark, feishu, wiki, backup, snapshot, delta, incremental, git, docx, disaster-recovery]
  related_skills: [lark-openapi-recipes, headless-config-backup, no-agent-cron-scripts, cron-timezone-discipline]
---

# Lark / Feishu wiki backup

Keep a **local, versioned mirror** of a Lark/Feishu wiki space. Each run pulls only
the nodes that were added or edited since last time, renames files in place when a
page is retitled, moves upstream-deleted pages to a `.trash/` folder, and commits the
result to git. An optional second step tars the mirror and uploads it offsite.

Two stages, run in order (both are [`no-agent`](../no-agent-cron-scripts/) cron jobs):

| Stage | Script | Does |
|---|---|---|
| 1. mirror | `scripts/lark_wiki_backup.py` | Lark space → `.docx` files on disk, delta only, git commit |
| 2. archive | `scripts/archive_snapshot.py` | the mirror → `.tar.gz` → rclone / Google Drive |

Stage 1 is the real backup (versioned, restorable per-file). Stage 2 is the offsite
belt-and-braces.

## Prerequisites

- A Lark/Feishu **app** (App ID + Secret) that is a **member of the target wiki
  space** — otherwise `get_node` / node listing return nothing (a scope wall, not an
  auth error; see [`lark-openapi-recipes`](../lark-openapi-recipes/)).
- App scopes: `wiki:wiki:readonly` (or `wiki:space:retrieve`), `docx:document:readonly`,
  `drive:export` / `drive:drive:readonly` for the export API. Publish a new app
  version after granting.
- `git` on the box. The mirror directory should be a git repo (or inside one).
- Python 3.9+, standard library only for stage 1.

## Stage 1 — mirror the space

```bash
export LARK_SPACE_ID=7xxxxxxxxxxxxxxxxxx        # the wiki space id
export LARK_ENV_FILE=~/.hermes/.env             # dotenv holding FEISHU_APP_ID / FEISHU_APP_SECRET
export BACKUP_ROOT=~/lark-wiki/space-name       # where the .docx tree is written (a git repo)
python3 scripts/lark_wiki_backup.py
```

Find `LARK_SPACE_ID`: `lark-cli wiki +space-list --as user`, or open any page in the
space and call `wiki/v2/spaces/get_node?token=<page token>` — the response has
`space_id`.

What it writes under `BACKUP_ROOT`:

```
<title path mirroring the wiki tree>.docx     # one file per docx node
.manifest.json                                 # {node_token: {path, last_edit_time, fetched}}  (git-ignored)
.trash/                                        # pages deleted upstream, kept for recovery (git-ignored)
```

The delta logic, per node:

| condition | action |
|---|---|
| token not in manifest, file absent | download |
| `last_edit_time` newer than manifest | re-download |
| `last_edit_time` same, title (path) changed | `os.rename` the local file — **no re-fetch** |
| unchanged | skip |
| in manifest but not in this run's tree | upstream-deleted → move to `.trash/`, drop from manifest |

First run (no manifest) downloads everything missing — same as a plain initial
export.

After a run with any change, it `git add` + `git commit` the `.docx` files with a
message like `delta docx: +3 ~5 renamed 1 pruned 0 (2026-09-07)`.

### Options (env)

| var | default | meaning |
|---|---|---|
| `LARK_SPACE_ID` | *(required)* | wiki space id |
| `BACKUP_ROOT` | `./lark-wiki-backup-data` | output dir (make it a git repo) |
| `LARK_ENV_FILE` | `~/.hermes/.env` | dotenv with `FEISHU_APP_ID` / `FEISHU_APP_SECRET` |
| `LARK_DOMAIN` | `lark` | `lark` (larksuite.com) or `feishu` (feishu.cn) |
| `LARK_EXCLUDE_PATHS` | *(none)* | comma-sep title segments to skip entirely (e.g. a huge transient "Test Plans" branch) |
| `LARK_TREE_FILE` | *(none)* | optional markdown tree snapshot to read the node list from instead of a live crawl — faster for very large spaces; the script self-heals it if missing |
| `LARK_GIT_COMMIT` | `1` | set `0` to skip the auto-commit |
| `LARK_NODE_TYPES` | `docx` | node `obj_type`s to export (docx only for now; `sheet`/`bitable` need a different export path) |

### Non-docx nodes

Sheets, Base, mindnotes are skipped by stage 1 (their export needs
`file_extension=xlsx` / a different API and they rarely belong in a doc mirror). If
you need them, export separately with `lark-cli sheets +workbook-export` on a
schedule and drop the files into `BACKUP_ROOT`.

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

- **One page:** copy the `.docx` back from the mirror (or from `.trash/` if it was
  deleted upstream), re-upload to Lark manually.
- **A branch or the whole space:** the git history is the source of truth —
  `git log`, `git checkout <sha> -- <path>`. The `.tar.gz` archives are the offsite
  fallback if the mirror box is lost.
- Lark has no bulk import API — restoring *into* Lark is manual per document. The
  value here is (a) you still have the content and its history, (b) you can diff what
  changed, (c) you can feed it to local tooling.

## Gotchas

- **Rate limits.** The export API throttles hard (error `99991400`). The script backs
  off and retries; a first run over a big space can take a while — let it.
- **`last_edit_time` is in the `get_node` response** already — the script uses it as
  the change key, so it costs one metadata call per node, not a content fetch.
- **Renames are cheap, moves within the tree are cheap** — both are local `os.rename`
  driven by the manifest. Only a real content edit re-downloads.
- **`.manifest.json` and `.trash/` must be git-ignored** — the script assumes this;
  add them to the mirror repo's `.gitignore`.
- **Export can return an empty file** for an empty doc (`job_status=2`,
  `file_token=""`) — the script skips those rather than writing a 0-byte file.
- **Keep the app a member of the space.** If it's removed, the next run sees an empty
  tree and would `.trash/` *everything*. The script refuses to prune when the crawl
  returns zero nodes — but check the run output.

See [`references/delta-design.md`](references/delta-design.md) for the manifest
schema, the self-healing tree snapshot, and why it's built this way.
