# Lark / Feishu OpenAPI — raw paths

Base host: `https://open.larksuite.com` (Lark international) or
`https://open.feishu.cn` (Feishu CN). All paths below are relative to the host.
All authenticated calls take `Authorization: Bearer <tenant_access_token>`.

## Auth

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/open-apis/auth/v3/tenant_access_token/internal` | `{app_id, app_secret}` | `tenant_access_token` (≈2h), `expire` |

## Wiki

| Method | Path | Params | Notes |
|---|---|---|---|
| GET | `/open-apis/wiki/v2/spaces` | `page_size`, `page_token` | needs `wiki:space:retrieve`; returns `items: []` if the app is in no space |
| GET | `/open-apis/wiki/v2/spaces/get_node` | `token=<wiki token>`, `obj_type=wiki` | resolve a link → `node` (`space_id`, `node_token`, `obj_type`, `obj_token`, `has_child`, `parent_node_token`, `title`) |
| GET | `/open-apis/wiki/v2/spaces/{space_id}/nodes` | `parent_node_token`, `page_size` (≤50), `page_token` | **list children** — this is the crawl primitive |
| — | `/open-apis/wiki/v2/spaces/{space_id}/nodes/{node}/children` | — | **404s — do not use** |

`obj_type` values: `docx`, `sheet`, `bitable`, `mindnote`, `file`, `slides`.

## Docx

| Method | Path | Notes |
|---|---|---|
| GET | `/open-apis/docx/v1/documents/{doc_token}/raw_content` | plain text, fast, **no image tokens** |
| GET | `/open-apis/docx/v1/documents/{doc_token}/blocks` | `document_revision_id=-1`, `page_size=50`, paginate `page_token`; structured blocks |

Block types seen in practice:

| `block_type` | meaning | handling |
|---|---|---|
| 1 | page (root) | container |
| 2 | text | `text.elements[].text_run.content` |
| 3–11 | headings h1–h9 | text-like |
| 12 | bullet list item | text-like |
| 13 | ordered list item | text-like |
| 14 | code block | `code.elements` |
| 27 | image | `image.token` → download via `drive/v1/medias` |
| 31 | table | children are cells |
| 32 | table cell | **skip**; recurse into children |

## Drive / media / export

| Method | Path | Notes |
|---|---|---|
| GET | `/open-apis/drive/v1/medias/{file_token}/download` | download an image or exported file; `file_token` is an id, not a URL |
| POST | `/open-apis/drive/v1/export_tasks` | `{file_extension, token, type}` → `data.ticket` |
| GET | `/open-apis/drive/v1/export_tasks/{ticket}` | `token=<obj_token>` → `data.result`; `job_status` may stay `0` while `file_token` is already usable; empty doc → `job_status=2`, `file_token=""` |

## Sheets

| Method | Path | Notes |
|---|---|---|
| GET | `/open-apis/sheets/v3/spreadsheets/{token}/sheets/query` | list sub-sheets (tabs) |
| GET | `/open-apis/sheets/v2/spreadsheets/{token}/values/{range}` | read a range, e.g. `Sheet1!A1:E9` |
| POST | `/open-apis/sheets/v3/spreadsheets/{token}/sheets/batch_update` | create/delete sub-sheets; **new `sheet_id` is at `data.replies[].addSheet.properties.sheetId`** (older `sheets.create` wrappers surface it as `data.sheet.sheet_id`, never `data.sheet_id`) |

Sheet/Base **content** reads need `bitable:app:readonly` / `base:record:retrieve` /
the sheets read scope — the bot token usually lacks these; metadata (list tabs) is
visible without them.

## IM

| Method | Path | Params | Notes |
|---|---|---|---|
| GET | `/open-apis/im/v1/messages` | `container_id_type=chat`, `container_id=<oc_...>`, `sort_type=ByCreateTimeDesc`, `page_size` (≤50), optional `user_id` + `user_id_type=open_id` | only messages after the bot joined |
| GET | `/open-apis/im/v1/chats/{chat_id}/members` | `member_id_type=open_id` | returns members as `open_id` — use to collect an allowlist |
| POST | `/open-apis/im/v1/messages` | `receive_id_type=chat_id`; body `{receive_id, msg_type:"text", content: json.dumps({text})}` | send as the bot (must be a chat member) |

## Contact

| Method | Path | Notes |
|---|---|---|
| GET | `/open-apis/contact/v3/users/{user_id}` | needs `contact:user.base:readonly` |

`open_id` (`ou_...`) and the tenant `user_id` (short hash) are **different id
spaces** and don't map without `contact:user.id:readonly` (often ungranted — API
error `99991672`).

## Common error codes

| code | meaning |
|---|---|
| `99991663` | invalid/expired `tenant_access_token` — re-mint |
| `99991672` | missing scope for the id conversion requested |
| `1254xxx` | docx/wiki permission or not-found |
| `permission denied: wiki space permission denied` | app not a member of the space |
