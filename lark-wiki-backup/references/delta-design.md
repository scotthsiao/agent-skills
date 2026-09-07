# Delta backup — design notes

Why `lark_wiki_backup.py` is built the way it is.

## Source adapters

The script is one delta engine with two pluggable adapters, each providing:

- **`list(token)`** → flat `[{token, title, depth, parent, ...}]` for the whole tree
- **`enrich(item, token, cache)`** → `{kind, content_token, change_key, ext, ...}` or
  `None` to skip. `kind` is `export` (Lark-native doc → export task) or `download`
  (uploaded file → fetched as-is).

| adapter | list API | auth | change key |
|---|---|---|---|
| `wiki` | `wiki/v2/spaces/{space}/nodes?parent_node_token=` (+ per-node `get_node`) | `tenant_access_token` | `last_edit_time` |
| `drive` | `drive/v1/files?folder_token=` (root from `drive/explorer/v2/root_folder/meta`) | `user_access_token` | `modified_time` |

Everything below — manifest, transitions, git, the trash guard — is adapter-agnostic.

## The manifest is the state

`.manifest.json` at the mirror root:

```json
{
  "<token>": {
    "path": "Methodology/BDD approach.docx",
    "change_key": "1724500000",
    "fetched": 1724500123
  }
}
```

- **key = the Lark token** (wiki `node_token` or drive file `token`), not the file
  path — so a page that is *retitled* (path changes) is still the same tracked
  entity. Titles are display; the token is identity.
- **`change_key`** is whatever the adapter reports as "last changed" —
  `last_edit_time` for wiki, `modified_time` for drive. It comes free in a response
  the crawl already makes, so change detection costs no extra content download.
  (Older manifests stored this as `last_edit_time`; `load_manifest` migrates them.)
- **`fetched`** is bookkeeping / debugging only.

The manifest is **git-ignored**. It's a local cache of "what I have", not part of the
backup. If it's lost, the next run rebuilds it: existing files are re-registered
(matched by path) without re-downloading, missing files are fetched.

## The four transitions

| manifest vs. live | meaning | action | cost |
|---|---|---|---|
| absent | new page | export + download | 1 export job |
| `last_edit_time` increased | edited | re-export + download | 1 export job |
| `last_edit_time` same, `path` differs | retitled / moved in tree | `os.rename` local file | free |
| `last_edit_time` same, `path` same | untouched | skip | free |
| in manifest, not in live tree | deleted upstream | move file to `.trash/`, drop from manifest | free |

On a stable space, a daily run is almost all "skip" + a couple of renames — a few
dozen cheap metadata calls, no downloads.

## The node list: live crawl vs. tree snapshot (wiki adapter)

Two ways to get the list of nodes:

1. **Live crawl** (default) — depth-first walk of
   `wiki/v2/spaces/{space}/nodes?parent_node_token=…`. Always current. Costs one
   list call per branch node. Fine for hundreds of nodes.

2. **Tree snapshot** (`LARK_TREE_FILE`) — a markdown file listing every node with its
   token and link, produced by a separate "rescan" job. Reading it is instant.
   Worth it only for very large spaces (thousands of nodes) where the crawl itself is
   slow, and where you already maintain such an index for other reasons.

If you point at a snapshot and it's missing or stale, the script falls back to a live
crawl. (The original in-house version also *self-healed* the snapshot by writing a
fresh one back after the fallback crawl — omitted here because it coupled the script
to a specific rescan module. If you keep a snapshot, regenerate it on its own
schedule.)

## The "refuse to trash everything" guard

If the app is removed from the wiki space, every API call still succeeds (valid
token) but the node list comes back **empty**. Without a guard, the delete-detection
logic would then move *every* mirrored file to `.trash/` and commit that.

So: **if the crawl returns zero nodes, the script exits non-zero and does nothing.**
A genuinely empty space is indistinguishable from a permissions loss, and the safe
assumption is the latter. Check the run output — a sudden "pruned N nodes" for large
N is the warning sign.

## Drive adapter specifics

- **Auth is user-scoped.** A `tenant_access_token` (bot) cannot see any user's
  personal Drive — the script takes a `user_access_token` via `LARK_USER_TOKEN` or,
  for cron, `LARK_USER_TOKEN_CMD` (a command printing a fresh one, since these expire
  in ~2h).
- **Root discovery:** `drive/explorer/v2/root_folder/meta` returns the personal
  "My Space" root token. `LARK_DRIVE_FOLDER_TOKEN` overrides it to mirror a specific
  (possibly shared) folder.
- **Mixed content:** `drive/v1/files` returns folders, Lark-native docs
  (`docx`/`sheet`/`bitable`/`mindnote`/`slides`), and uploaded `file`s. Native docs
  go through the same export→poll→download as wiki; uploaded files are fetched
  verbatim from `drive/v1/files/{token}/download` with their original extension.
  Shortcuts are not followed. Unknown types are skipped with a warning.
- **`modified_time` is coarser than wiki's `last_edit_time`** — it can bump on some
  metadata-only changes, so the drive adapter re-downloads slightly more than
  strictly necessary. Acceptable; still far cheaper than a full re-export.

## Renames vs. content edits (wiki)

Lark's `last_edit_time` updates on a **content** edit but not on a pure **retitle**
(the title lives on the wiki node, the body in the docx object). So:

- retitle only → `last_edit_time` unchanged, `path` changes → local `os.rename`, no
  fetch. Git sees a rename.
- content edit → `last_edit_time` bumps → re-download regardless of title.
- both → the `last_edit_time` branch wins (re-download to the new path).

This is why the manifest stores `path` separately from `last_edit_time` — they move
independently.

## Git commit

After any change the script stages the `.docx` tree (excluding `.manifest.json` and
`.trash/`) and commits with a one-line stat:

```
delta docx: +3 ~5 renamed 1 pruned 0 (2026-09-07)
```

Set `LARK_GIT_COMMIT=0` to leave staging/committing to a wrapper (e.g. if you want to
sign commits, or push, or bundle with other changes).

`git log --follow -- <path>` then gives you the edit history of any single page;
`git checkout <sha> -- <path>` restores a prior version.

## `.gitignore` for the mirror repo

```
.manifest.json
.trash/
*.tmp
```

## Rate limiting

The Drive **export** API (`/drive/v1/export_tasks`) throttles aggressively — error
code `99991400`. `call()` detects it and sleeps `min(8 + 6*attempt, 60)` seconds,
separate from the generic exponential backoff. A first full export of a large space
can take tens of minutes; subsequent delta runs barely touch it.
