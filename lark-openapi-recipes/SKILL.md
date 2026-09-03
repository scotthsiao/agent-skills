---
name: lark-openapi-recipes
description: >-
  Read Lark / Feishu (飞书) wiki spaces, docx documents, spreadsheets, Base, and group
  message history from an agent, using the OpenAPI directly or the official
  @larksuite/cli. Use when a user pastes a Lark/Feishu link and wants it read or
  summarized, when crawling a wiki knowledge base, when the built-in feishu_doc_read
  tool returns "not in a Feishu comment context", when hitting wiki/Base permission
  errors, or when extracting docx content with images or tables.
version: 1.0.0
license: MIT
metadata:
  tags: [lark, feishu, wiki, docx, sheets, bitable, openapi, larksuite-cli, crawl, ingest]
  related_skills: [messaging-gateway-bot-triage]
---

# Lark / Feishu OpenAPI recipes

Everything needed to read content out of a Lark/Feishu tenant as an agent. Two
routes; pick per situation:

| Route | Reads as | Good for | Blocked on |
|---|---|---|---|
| **A. Direct OpenAPI** with a `tenant_access_token` | the app/bot | docx bodies, crawling a wiki the bot can already see, IM history | whole-space listing, Base/Sheets *data* (scope walls) |
| **B. [`@larksuite/cli`](https://github.com/larksuite/cli)** with `--identity user-default` | the user (OAuth) | everything the user can see: all spaces, Sheets, Base, Drive | needs a one-time browser device-login |

Route A needs nothing installed. Route B is the clean fix for the scope walls, but
grants the agent the user's privileges — confirm intent first.

## Route A — direct OpenAPI

### Host

`larksuite.com` tenant → `https://open.larksuite.com`.
`feishu.cn` tenant → `https://open.feishu.cn`. Mismatched host = auth failure. The
share-link domain tells you which.

### Mint a token

`POST /open-apis/auth/v3/tenant_access_token/internal` with
`{"app_id": ..., "app_secret": ...}` → `tenant_access_token` (valid ~2h). Creds
come from your gateway's `.env` (`FEISHU_APP_ID` / `FEISHU_APP_SECRET`) — read them
programmatically, never echo the secret.

`scripts/lark_token.py` does this; the other scripts import it.

### Resolve a wiki link → node

`GET /open-apis/wiki/v2/spaces/get_node?token={WIKI_TOKEN}&obj_type=wiki` →
`node` with `title`, `has_child`, `obj_type` (`docx` | `sheet` | `bitable`),
`obj_token`, `space_id`, `parent_node_token`.

### Crawl a wiki tree

**List children via the `parent_node_token` query param, not a `/children` path** —
`GET /open-apis/wiki/v2/spaces/{SPACE_ID}/nodes?parent_node_token={NODE}&page_size=50`.
The `.../nodes/{node}/children` sub-path **404s**. Walk depth-first; stop when
`has_child` is false. `scripts/lark_wiki_crawl.py` does this and can emit an index
tree or full content.

### Read a docx body

- Plain text: `GET /open-apis/docx/v1/documents/{OBJ_TOKEN}/raw_content` — fast, but
  **contains no image tokens**.
- Structured: `GET /open-apis/docx/v1/documents/{OBJ_TOKEN}/blocks?document_revision_id=-1&page_size=50`
  (paginate). Block type map:

  | `block_type` | meaning | handling |
  |---|---|---|
  | 27 | image | has `image.token`; download via `GET /open-apis/drive/v1/medias/{token}/download` |
  | 32 | table_cell | skip (content is in child blocks) |
  | 13 | ordered list item | (also list-like) |
  | 2 | text | the common case |

### Export a docx/sheet as a file

`POST /open-apis/drive/v1/export_tasks` → `data.ticket`. Poll
`GET /open-apis/drive/v1/export_tasks/{ticket}?token={OBJ_TOKEN}` → `data.result`.
Quirk: `job_status` often stays `0` while `file_token` is already populated — once
`file_token` is non-empty you can download: `GET /open-apis/drive/v1/medias/{file_token}/download`.
An empty document comes back `job_status=2` with `file_token=""` → skip it.

### Read group message history

`GET /open-apis/im/v1/messages?container_id_type=chat&container_id={CHAT_ID}&sort_type=ByCreateTimeDesc&page_size=50`
(optional `user_id={OPEN_ID}` to filter by sender). Only messages after the bot
joined the chat are visible. `scripts/lark_im_history.py`.

### Create a sheet (response shape gotcha)

`sheets.create` (or `POST .../sheets/v3/spreadsheets/{token}/sheets/batch_update`)
returns the new `sheet_id` at **`r['data']['sheet']['sheet_id']`**, not
`r['data']['sheet_id']`.

### Text cleaning

Lark docx text is littered with invisible characters that break parsers — strip them:

```python
text = text.replace(" ", " ").replace("​", "")   # NBSP, ZWSP
```

## Route B — @larksuite/cli

```bash
npx @larksuite/cli@latest install          # Node >= 18; Go NOT required
lark-cli config bind --source hermes --identity user-default
#   ^ inside a Hermes context, `config init --new` is REFUSED (would shadow the
#     existing app binding) — always `config bind`.
lark-cli auth login --no-wait --json       # prints a verification URL + device code
#   send the URL to the user; do NOT open it with a browser tool
lark-cli auth qrcode --output ./qr.png "<verification_url>"   # relative path only
#   ... user authorizes ...
lark-cli auth login --device-code <CODE>   # resume, completes
lark-cli auth status --as user             # confirm granted scopes
```

Each `auth login` restart **invalidates the previous device code** — don't retry on a
short timeout; wait for the user's confirmation.

Reading:

```bash
lark-cli wiki +space-list --as user                       # ALL spaces (tenant token can't)
lark-cli wiki +node-list --space-id <SID> --parent-node-token <NODE> --as user
lark-cli sheets +workbook-info --spreadsheet-token <TOK> --as user --format table
lark-cli sheets +cells-get --spreadsheet-token <TOK> --sheet-id <SID> --range "A1:E9" --as user
lark-cli sheets +workbook-export --spreadsheet-token <TOK> --format csv --as user
```

Resource tokens go via flags (`--spreadsheet-token`, `--sheet-id`, `--url`) —
positional args are rejected.

Posting to a group: use **`--as bot`** (the bot must be a group member). `--as user`
fails with `missing required scope(s): im:message.send_as_user`.

## Scope walls (Route A) and what fixes each

| Symptom | Cause | Fix |
|---|---|---|
| `GET /wiki/v2/spaces` → `items: []` | the app is not a member of any space (and lacks `wiki:space:retrieve`). **Not** an auth failure — the token is valid. | add the app to the space's members, or use Route B |
| `permission denied: wiki space permission denied` | same | same |
| `get_node` works but listing the whole space doesn't | you can traverse a space the app *can see* (e.g. a group wiki the app is in) without space-list scope | crawl by traversal from a known node |
| `Access denied ... scope required: [bitable:app:readonly]` | Base/Sheets **content** needs `bitable:app:readonly` / `base:record:retrieve`; metadata is visible without it | grant the scope + republish the app version, or Route B |
| docx reads fine, sheets don't | docx read is available by default; sheet data isn't | Route B |

## `feishu_doc_read` is useless in a DM

The bundled `feishu_doc_read` tool only initializes inside a **document comment /
@-mention context**. In a DM it returns `Feishu client not available (not in a
Feishu comment context)` — a hard limitation, not a token problem. Don't retry it;
use Route A or B, or ask the user to @-mention the bot inside the doc.

## Incremental ingest

When syncing a wiki into local storage, don't re-fetch everything daily. Record each
node's `last_edit_time` in the local file's frontmatter; on the next run, compare and
skip unchanged nodes. For a large space, write an **index tree** (titles + node
tokens + links) for everything and fetch full bodies only for a curated set —
flowing/transient pages (test plans, meeting minutes) don't belong in permanent
storage.

## References

- [`references/api-paths.md`](references/api-paths.md) — every raw endpoint used here,
  with parameters and response shapes.
- [`references/crawl-skeleton.md`](references/crawl-skeleton.md) — a minimal
  stdlib-only recursive wiki crawler you can paste and adapt.
