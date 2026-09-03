---
name: gdrive-token-refresh
description: >-
  Keep a headless Google Drive / Google API integration authenticated for the long
  run. Use when a backup or automation cron has stopped with "invalid_grant: Token
  has been expired or revoked", when a Google refresh token keeps dying every ~7
  days, when setting up a weekly token health check, or when deciding between a
  Testing OAuth app, a published app, and a service account for an unattended script.
version: 1.0.0
license: MIT
metadata:
  tags: [google, oauth, drive, refresh-token, invalid-grant, cron, backup, maintenance, service-account]
  related_skills: [google-oauth-headless, headless-config-backup]
---

# Google Drive token refresh (the recurring chore)

An unattended Google integration — a nightly Drive backup, a Sheets sync — runs on a
`refresh_token`. That token can silently die, and the first you hear of it is a
backup that hasn't run for a week. This skill is about **not being surprised**: why
it dies, how to detect it early, how to recover, and how to make it stop happening.

## Why the token dies

| Cause | Signature | Frequency |
|---|---|---|
| **OAuth app is in "Testing" publishing status** | `invalid_grant` exactly **7 days** after the last consent, every time | the usual one |
| Refresh token unused for ~6 months | `invalid_grant` after a long quiet period | rare |
| User revoked access in Google Account → Security → Third-party access | `invalid_grant` right after they clicked | occasional |
| >50 refresh tokens issued for the same client+user | oldest tokens silently invalidated | only if you re-consent a lot |
| Client secret rotated / OAuth client deleted in GCP | `invalid_client` / `deleted_client` | self-inflicted |

The **7-day Testing expiry** is the one that catches everyone. A Google Cloud OAuth
consent screen left in "Testing" issues refresh tokens that expire in 7 days, full
stop. Perfect for the first test, useless for a cron job.

## Detect it early — weekly health check

Run `scripts/check_token.py` on its own schedule (weekly is plenty) and have it shout
on failure. It loads the stored token, forces a refresh, and reports how much
head-room is left.

```bash
python3 scripts/check_token.py            # exit 0 = healthy, 1 = broken/expiring
python3 scripts/check_token.py --json     # machine-readable
```

Env:

| var | default | meaning |
|---|---|---|
| `TOKEN_FILE` | `./token.json` | the authorized-user JSON |
| `OAUTH_SCOPES` | `https://www.googleapis.com/auth/drive.file` | space-separated |
| `ALERT_CMD` | *(none)* | shell command run with the status line on stdin when unhealthy |

Wire the alert to whatever you already have — a messaging push, an email, a file
touch a dashboard watches:

```bash
ALERT_CMD='xargs -0 my-notify --title "Drive token"' \
  python3 scripts/check_token.py
```

Put this check **beside** the backup job, a day earlier. If the backup runs Sunday,
check Saturday — that gives you a day to re-consent before anything is missed.

## Recover a dead token

1. Re-run the consent flow — see [`google-oauth-headless`](../google-oauth-headless/):
   `pkce_auth.py stage1` → open URL, consent → `pkce_auth.py stage2 "<redirect>"`.
   The auth URL **must** include `prompt=consent` or you get an access token with no
   new refresh token.
2. `python3 scripts/check_token.py` — confirm healthy.
3. Run the real job once by hand to be sure.
4. If this is the *second* time in a month, stop patching and apply a permanent fix
   below.

## Make it stop — permanent fixes, best first

### 1. Publish the OAuth app (removes the 7-day expiry) — 5 minutes, free

GCP Console → **APIs & Services → OAuth consent screen → Publishing status →
"PUBLISH APP"** (moves Testing → In production).

- Refresh tokens issued after this **do not expire** on a schedule.
- You do **not** need Google verification for personal use: an unverified published
  app still works, capped at 100 users, showing an "unverified app" screen you click
  through once. For a solo backup that is completely fine.
- Re-consent once after publishing so you're holding a non-expiring token.

### 2. Use a service account (no user token at all) — best for truly unattended

For server-to-server access to a Drive folder you control:

1. GCP Console → IAM & Admin → Service Accounts → create one → create a **JSON key**.
2. Share the target Drive folder with the service account's email
   (`name@project.iam.gserviceaccount.com`) as Editor. (Or, on Google Workspace, set
   up domain-wide delegation.)
3. Authenticate with the key file — no consent screen, no refresh token, nothing to
   expire:
   ```python
   from google.oauth2 import service_account
   creds = service_account.Credentials.from_service_account_file(
       "sa-key.json", scopes=["https://www.googleapis.com/auth/drive"])
   ```
4. `rclone` supports this directly: `service_account_file` in the remote config.

Caveats: a service account has **its own** 15 GB-ish Drive quota and cannot own files
in a normal consumer Drive — it writes into folders *shared with it* by a real
account, and those files count against the sharing account's quota. For a backup
folder this is the right model.

### 3. If you use rclone: same root cause, same fixes

`rclone`'s built-in Google client is convenience-only and subject to the same Testing
expiry. Either supply your own published-app `client_id`/`client_secret` in the
remote, or switch the remote to `service_account_file`.

## Checklist for a new headless Google integration

- [ ] OAuth client type = **Desktop app**
- [ ] Consent screen **published** (not Testing) — or using a service account
- [ ] Stored `token.json` contains a `refresh_token` (`check_token.py` verifies)
- [ ] `check_token.py` scheduled weekly, one day before the job it protects
- [ ] `ALERT_CMD` wired to a channel you actually read
- [ ] `client_secret.json` / service-account key is **not** inside the backup set it
      protects
