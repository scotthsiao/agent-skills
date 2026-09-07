# lark-wiki-backup — setup runbook

A step-by-step for standing up a Lark/Feishu backup on a headless box or container.
`SKILL.md` is the reference (and what an agent reads); this is the human walk-through.

Two things can be backed up — pick one to start:

- **A wiki space** (知识库) — team knowledge base. Auth = an app that's a space member.
- **A personal / shared Drive folder** (我的空间) — your own cloud files. Auth = your
  own login (OAuth).

Both produce the same thing: a local git repo of the content, updated incrementally,
with an optional offsite `.tar.gz`.

---

## Part A — back up a personal / shared Drive folder

### 1. Prerequisites

- `git`, Python 3.9+ on the box.
- A Lark app you can configure (the same App ID / Secret used elsewhere is fine — you
  are not adding a bot, just using it as an OAuth client).
- The account whose Drive you want to back up, and a browser **once** for the login.

### 2. Configure the app in the developer console

Open <https://open.larksuite.com/> (or `open.feishu.cn`) → your app.

1. **Security settings → Redirect URLs** → add exactly:
   ```
   http://localhost:9899
   ```
2. **Permissions & Scopes** → add:
   ```
   offline_access
   drive:drive:readonly
   docx:document:readonly
   sheets:spreadsheet:readonly
   drive:export:readonly
   ```
   `offline_access` is required — without it Lark won't issue a refresh token and the
   cron job can't renew itself.
3. **Version Management** → create and publish a version. Scope changes don't take
   effect until published.

### 3. Get the code and the token helper

```bash
git clone https://github.com/scotthsiao/agent-skills ~/src/agent-skills
cd ~/src/agent-skills/lark-wiki-backup
```

### 4. Give the helper your app credentials

Either export them:

```bash
export FEISHU_APP_ID=cli_xxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxx
```

…or point at a dotenv that has `FEISHU_APP_ID` / `FEISHU_APP_SECRET`:

```bash
export LARK_ENV_FILE=~/.hermes/.env
```

### 5. Authorize once (needs a browser)

```bash
python3 scripts/lark_user_token.py authorize
```

- Open the printed URL in a browser.
- Log in **as the account whose Drive you're backing up**, and approve.
- The browser ends on a "can't reach this page" at
  `http://localhost:9899/?code=...&state=...` — that's expected. Copy the whole
  address-bar URL.

```bash
python3 scripts/lark_user_token.py exchange "http://localhost:9899/?code=...&state=..."
```

This prints the first access token and saves the (rotating) refresh token to
`~/.config/lark/user_token.json` (mode 600).

### 6. Confirm the token refreshes

```bash
python3 scripts/lark_user_token.py
```

Should print a fresh access token and nothing else. This exact command is what the
backup job will call.

### 7. First backup — a small folder

Don't point it at your whole Drive yet. Grab one folder's token from its URL
(`https://…/drive/folder/<TOKEN>`):

```bash
export LARK_SOURCE=drive
export BACKUP_ROOT=~/lark-drive/test
export LARK_USER_TOKEN_CMD="python3 $PWD/scripts/lark_user_token.py"
export LARK_DRIVE_FOLDER_TOKEN=fldxxxxxxxx      # the small test folder

mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"

python3 scripts/lark_wiki_backup.py
```

Check `$BACKUP_ROOT` — you should see the folder's docs mirrored, a git commit, and
a `.manifest.json`. Native docs come out as `.docx` / `.xlsx` / `.pdf`; uploaded
files keep their original name.

### 8. Point it at the whole Drive

Drop `LARK_DRIVE_FOLDER_TOKEN` and change `BACKUP_ROOT`:

```bash
unset LARK_DRIVE_FOLDER_TOKEN
export BACKUP_ROOT=~/lark-drive/my-space
mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"
python3 scripts/lark_wiki_backup.py
```

With no folder token it starts from your personal root ("My Space") and walks
everything.

### 9. Schedule it

Two jobs, both "run a script, deliver its stdout, don't call the model":

| job | schedule (local) | command |
|---|---|---|
| mirror | daily, early | `lark_wiki_backup.py` |
| archive | daily, after the mirror | `archive_snapshot.py` |

Cron runs in **UTC** — convert your local time (see the `cron-timezone-discipline`
skill; e.g. Taipei 05:15 = `15 21 * * *`).

The job's environment needs: `LARK_SOURCE=drive`, `BACKUP_ROOT`,
`LARK_USER_TOKEN_CMD`, and either the two `FEISHU_APP_*` vars or `LARK_ENV_FILE`.

**Offsite archive (optional):**

```bash
export ARCHIVE_SRC=~/lark-drive/my-space
export ARCHIVE_NAME=my-lark-drive
export ARCHIVE_UPLOAD=rclone            # needs an rclone remote; see headless-config-backup
export RCLONE_REMOTE=gdrive
export RCLONE_REMOTE_DIR="Lark Drive Backups"
python3 scripts/archive_snapshot.py
```

### 10. Maintenance

- The refresh token lasts ~30 days of inactivity and is **rotated on every use** —
  as long as the daily job runs, it stays alive. If it lapses (or you revoke it),
  redo step 5.
- If a run prints a large `pruned N`, stop and check — it usually means the token
  lost a scope or access, not that N files were really deleted. The script refuses
  to run at all if the listing comes back empty.

---

## Part B — back up a wiki space

### 1. Make the app a member of the space

In the wiki space → **Settings → Members** → add your app. Without this, the node
listing returns nothing.

App scopes (console, then publish a version): `wiki:wiki:readonly`,
`docx:document:readonly`, `drive:export:readonly`.

### 2. Find the space id

```bash
lark-cli wiki +space-list --as user
```

or open any page in the space and call
`wiki/v2/spaces/get_node?token=<page token>` — the response has `space_id`.

### 3. Run

```bash
export LARK_SPACE_ID=7xxxxxxxxxxxxxxxxxx
export LARK_ENV_FILE=~/.hermes/.env         # holds FEISHU_APP_ID / FEISHU_APP_SECRET
export BACKUP_ROOT=~/lark-wiki/team-space

mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"

python3 scripts/lark_wiki_backup.py
```

No user token needed — the app's own `tenant_access_token` is enough for a wiki
space it belongs to. Schedule and archive exactly as in Part A steps 9–10.

---

## Restore

- **One page/file:** copy it back from `$BACKUP_ROOT` (or `.trash/` if it was deleted
  upstream), re-upload to Lark by hand.
- **History:** `git -C "$BACKUP_ROOT" log --follow -- "<path>"`,
  `git checkout <sha> -- "<path>"`.
- Lark has no bulk import — restoring *into* Lark is manual per document. What you
  keep is the content, its full edit history, and something local tooling can read.

## Environment variable reference

See the "Options (env)" table in [`SKILL.md`](SKILL.md).
