---
name: headless-config-backup
description: >-
  Back up an application or agent's configuration (not its rebuildable caches) to
  cloud storage on a schedule, from a headless server or container. Use when asked to
  "back up my agent / config to Google Drive / the cloud", to set up an automated
  daily/weekly backup, to add local retention or a completion notification, or to
  decide what is worth backing up versus what regenerates itself.
version: 1.0.0
license: MIT
metadata:
  tags: [backup, cron, google-drive, rclone, retention, headless, config, disaster-recovery]
  related_skills: [google-oauth-headless, gdrive-token-refresh, no-agent-cron-scripts, cron-timezone-discipline]
---

# Headless config backup

Package the *config* of a long-running app — the parts you cannot regenerate — and
push it to cloud storage on a schedule. `scripts/config_backup.py` does the whole
job: select → archive → upload → prune → notify.

## What to back up (and what not to)

**Back up** — anything that represents state or intent you'd have to rebuild by hand:

- config files (`config.yaml`, `.env` — see the secrets note below), prompt/persona
  files
- credentials / auth state (`auth.json`, token stores) — so a restore is usable
- the small databases that hold real state (task boards, session metadata, schedule
  definitions)
- user-authored content: skills, scripts, memories, notes

**Skip** — anything the app recreates on next run. Backing these up bloats the
archive and slows every run:

- `node_modules/`, virtualenvs, language runtimes
- `*cache*/`, `__pycache__/`, downloaded models, audio/image caches
- logs
- `*.db-wal` / `*.db-shm` (SQLite side files — back up the `.db`, let these rebuild)
- the backup output directory itself (never let a backup archive a previous backup)

## Two tiers

- **daily** — a fast, minimal set: just config + auth + state DBs + user content.
- **weekly** — a fuller sweep of the home dir with the heavy dirs pruned.

Both keep a rolling local copy and upload the archive.

## Usage

```bash
python3 scripts/config_backup.py daily
python3 scripts/config_backup.py weekly
```

Configure with env vars or a `backup.toml` next to the script:

| var | default | meaning |
|---|---|---|
| `BACKUP_HOME` | `~/.hermes` | the directory being backed up |
| `BACKUP_OUT` | `~/backups` | where local archives land |
| `BACKUP_DAILY_INCLUDE` | see script | comma-separated paths (relative to HOME) for the daily set |
| `BACKUP_PRUNE_DIRS` | `node_modules,cache,__pycache__,logs,...` | dir names never traversed |
| `BACKUP_KEEP` | `7` | local archives to retain per tier |
| `BACKUP_UPLOAD` | `rclone` | `rclone`, `gdrive-api`, or `none` |
| `RCLONE_REMOTE` | `gdrive` | for `BACKUP_UPLOAD=rclone` |
| `RCLONE_REMOTE_DIR` | `AppBackups` | remote folder |
| `GDRIVE_TOKEN_FILE` | `~/.config/app/token.json` | for `BACKUP_UPLOAD=gdrive-api` |
| `BACKUP_NOTIFY_CMD` | *(none)* | shell command; gets a one-line summary on stdin (NUL-terminated) |

## Wiring it as a cron job

This is a [`no-agent-cron-scripts`](../no-agent-cron-scripts/) job — it should run the
script directly and deliver stdout, never wake the LLM. Two schedules:

```
daily   at 02:00 local  ->  config_backup.py daily
weekly  Sat 03:00 local ->  config_backup.py weekly
```

Cron runs in UTC — convert those local times per
[`cron-timezone-discipline`](../cron-timezone-discipline/) (e.g. Taipei 02:00 =
`0 18 * * *`).

## Secrets

The `.env` / credential files are the point of the backup — a restore without them is
useless — but that means **the archive contains secrets**. Therefore:

- The cloud folder must be private (not "anyone with link").
- Consider `age` / `gpg` encrypting the archive before upload if the storage is
  shared. The script has an `BACKUP_ENCRYPT_TO` hook (age recipient) for this.
- Never commit an archive to git.

## The re-exec-under-the-app-venv trick

If the upload path (`gdrive-api`) needs libraries that only exist in the app's own
virtualenv, the script re-execs itself under that interpreter when
`BACKUP_VENV_PYTHON` is set — so a bare `python3 config_backup.py` from cron still
works. `rclone` upload has no Python deps and sidesteps this entirely.

## Verify by restoring

A backup you've never restored is a hypothesis. Once a quarter: pull the latest
archive onto a scratch box, extract, and confirm the app starts against it. Keep
notes on any path rewriting a cross-machine restore needs (absolute paths in configs,
venv locations).
