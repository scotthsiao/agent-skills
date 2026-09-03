---
name: google-oauth-headless
description: >-
  Complete a Google OAuth 2.0 / Drive API consent flow on a machine with no browser
  (a server or container running cron backups). Use when a script needs Google Drive
  or any Google API auth and there is no display, when google-auth-oauthlib
  InstalledAppFlow fails with "Invalid code verifier" / InvalidGrantError across two
  processes, or when setting up an rclone Google Drive remote headless.
version: 1.0.0
license: MIT
metadata:
  tags: [google, oauth, drive, headless, rclone, pkce, backup, cron]
  related_skills: [gdrive-token-refresh, headless-config-backup]
---

# Google OAuth (headless)

Get a working Google API token on a box with no browser. Covers two flows:

- **Path A — manual PKCE**, producing a `token.json` for `google-api-python-client`.
- **Path B — out-of-band code paste**, writing an `rclone` Google Drive remote.

Pick A if your script uses the Google client libraries; pick B if it shells out to
`rclone`.

## When to use

- A cron/automation script needs Drive (or Gmail, Calendar, …) and the machine has no
  display.
- `InstalledAppFlow.from_client_secrets_file(...)` gives a URL that, after you paste
  the `code` back, fails with `invalid_grant: Invalid code verifier`.
- You have a **Desktop-app** `client_secret.json` but no `token.json`.
- You need `rclone` to reach Google Drive on a headless host.

## Why `InstalledAppFlow` fails here

`InstalledAppFlow` generates the PKCE `code_verifier` internally and does not expose
it for you to persist. When you split "print the auth URL" and "exchange the code"
into **two processes** — which you must when an agent can't hold an interactive stdin
— the second process has no `code_verifier`, so Google rejects the exchange. Reusing
a spent `code` fails too (codes are single-use).

The fix: **own the PKCE verifier yourself.** Generate `code_verifier` +
`code_challenge`, persist the verifier, print the URL, and exchange in a second stage
with a plain `requests.post`.

## Path A — manual PKCE → `token.json`

```bash
# stage 1: print the auth URL, persist the verifier
python3 scripts/pkce_auth.py stage1

# ...open the URL in ANY browser, consent, copy the final redirect URL...

# stage 2: exchange the pasted redirect URL for a token
python3 scripts/pkce_auth.py stage2 "http://localhost/?code=...&state=..."

# verify the token still refreshes
python3 scripts/pkce_auth.py verify
```

Env overrides (all optional):

| var | default | meaning |
|---|---|---|
| `CLIENT_SECRET` | `./client_secret.json` | Desktop-app OAuth client file |
| `STATE_FILE` | `/tmp/oauth_state.json` | where the verifier is stashed between stages |
| `TOKEN_FILE` | `./token.json` | output, in `Credentials.from_authorized_user_file` shape |
| `OAUTH_SCOPES` | `https://www.googleapis.com/auth/drive.file` | space-separated |

Your script then loads it normally:

```python
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
creds = Credentials.from_authorized_user_file("token.json",
            ["https://www.googleapis.com/auth/drive.file"])
if not creds.valid:
    creds.refresh(Request())          # uses the refresh_token
```

## Path B — out-of-band paste → `rclone` remote

```bash
export GD_CLIENT_ID=...           # from your Desktop-app OAuth client
export GD_CLIENT_SECRET=...
python3 scripts/oob_rclone_auth.py url          # prints the consent URL
python3 scripts/oob_rclone_auth.py token <CODE> # writes ~/.config/rclone/rclone.conf [gdrive]
rclone lsd gdrive:                               # verify
```

This uses `redirect_uri = urn:ietf:wg:oauth:2.0:oob`, so Google shows the `code` on
screen for you to copy — no `localhost` callback needed.

## Gotchas

- **The OAuth client MUST be type "Desktop app"** (not "Web application"). Web
  clients reject `http://localhost` redirects and this PKCE pattern.
- **`redirect_uri` must exactly match** what the client registered. Desktop app =
  `http://localhost` (Path A) or the `oob` urn (Path B). `Missing required parameter:
  redirect_uri` means it was dropped from the URL — regenerate.
- **One `code` = one exchange.** After any failed exchange the code is spent.
  Generate a fresh URL (fresh state + challenge) and consent again.
- **`token.json` must contain a `refresh_token`** or headless renewal is impossible.
  It is only returned on the *first* consent, or whenever `prompt=consent` is in the
  auth URL — both scripts here force `prompt=consent`.
- **Pass scopes as a list, not a bare string**, to the google-auth library — a
  string is treated as an iterable of characters and yields `invalid_scope`.
- **"App isn't verified"** is expected for your own unpublished project — click
  through it. To stop refresh tokens expiring every 7 days, see
  [`gdrive-token-refresh`](../gdrive-token-refresh/).
- **Lost `client_secret.json`?** GCP Console → APIs & Services → Credentials → the
  Desktop client → download (⬇). A backup script should never archive this file into
  the same bucket it protects.

## Recovery: `invalid_grant` / revoked refresh token

When a script fails with `RefreshError: invalid_grant: Token has been expired or
revoked`, the stored `refresh_token` is dead (expired on a "Testing" app, revoked
manually, or reclaimed by Google after ~6 months idle). Re-run the same flow above
(`stage1` → consent → `stage2`) to mint a fresh pair. Then confirm with
`scripts/pkce_auth.py verify`. For prevention, see
[`gdrive-token-refresh`](../gdrive-token-refresh/).
