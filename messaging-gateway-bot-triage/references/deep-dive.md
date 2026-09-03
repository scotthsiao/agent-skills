# Gateway triage — deep dive

Longer notes that don't belong in the decision tree.

## Lark/Feishu console checklist (the upstream side of Layer 2)

Developer console (`open.larksuite.com` intl / `open.feishu.cn` CN):

1. **Bot capability** enabled (應用能力 → Bot).
2. **Permissions / scopes**: `im:message`, `im:message:send_as_bot`, `im:resource`,
   `im:chat`, `im:chat:readonly`. Useful extras: `im:message.reactions:readonly`,
   `contact:user.base:readonly`.
3. **Events & Callbacks**: subscription mode = **Long Connection (WebSocket)**;
   subscribe `im.message.receive_v1`; for groups also enable receiving group
   messages.
4. **Version Management**: create + publish a version. For enterprise apps this
   needs admin approval. The *online* version must list the event — a version marked
   "published" can still be "under review".
5. **App availability** must include your account (add yourself as a tester, or
   publish to the org).
6. Disable any welcome message / auto-reply / bot menu, and in
   `admin.larksuite.com` unbind the bot from any Helpdesk (服务台).

## `.env` variables (Lark/Feishu)

| var | effect |
|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | app credentials |
| `FEISHU_DOMAIN` | `lark` (larksuite.com) or `feishu` (feishu.cn) — must match the tenant |
| `FEISHU_CONNECTION_MODE` | `websocket` |
| `FEISHU_ALLOW_ALL_USERS` | `true` = anyone may DM; `false` = only `FEISHU_ALLOWED_USERS` |
| `FEISHU_ALLOWED_USERS` | comma-sep ids, no spaces; put **both** open_id and tenant user_id per member |
| `FEISHU_GROUP_POLICY` | `open` / `allowlist` / `disabled`. `disabled` = bot never receives group messages (DMs still work) — good for one-way broadcast |
| `FEISHU_REQUIRE_MENTION` | default `true`: in groups the bot only acts when @-mentioned. Does **not** block `@all` (the bot's own id is in that mention list) |
| `FEISHU_HOME_CHANNEL` | `oc_…` chat that cron / `send` posts to by default |

`FEISHU_ALLOW_ALL_USERS` (per-platform) beats the global
`GATEWAY_ALLOW_ALL_USERS` warning — setting the platform var is sufficient.

## Pairing flow (allowlist alternative)

1. `FEISHU_ALLOW_ALL_USERS=false` and `FEISHU_ALLOWED_USERS=` (empty) → pairing mode.
2. Restart. User DMs the bot → it replies with a pairing code.
3. Owner: `<app> pairing approve feishu <CODE>` → approved, persisted in gateway
   state (not `.env`). Re-pair if that state is wiped.

Use pairing for ad-hoc/unknown users; use an explicit `FEISHU_ALLOWED_USERS` list
for a known internal team (survives restarts via `.env`).

## Layer 4 — full fix + verify probe

```bash
cp config.yaml config.yaml.bak.$(date +%Y%m%d_%H%M%S)

python3 - <<'PY'
import yaml, ast
p = 'config.yaml'
raw = yaml.safe_load(open(p))
for path in [('agent', 'disabled_toolsets'), ('platform_toolsets', 'feishu')]:
    node = raw
    for k in path[:-1]:
        node = node.get(k, {})
    cur = node.get(path[-1])
    if isinstance(cur, str):
        node[path[-1]] = ast.literal_eval(cur)   # "['a','b']" -> ['a','b']
yaml.safe_dump(raw, open(p, 'w'), sort_keys=False, allow_unicode=True,
               default_flow_style=False)
print("fixed")
PY
```

Verify (adjust import paths to your framework):

```python
import yaml, sys
sys.path.insert(0, '/usr/local/lib/hermes-agent')
raw = yaml.safe_load(open('config.yaml'))
from hermes_cli.tools_config import _get_platform_tools
from toolsets import resolve_toolset
tools = set()
for ts in _get_platform_tools(raw, 'feishu'):
    tools.update(resolve_toolset(ts))
for bad in ['cronjob', 'execute_code', 'terminal', 'web_search']:
    print(f"{bad:14} blocked? {bad not in tools}")
print("read_file present (knowledge source kept)?", 'read_file' in tools)
```

The CLI/admin session is a **separate process** and keeps full tools — restricting
the gateway agent does not lock you out of your own terminal.

## Effective-tools formula

```
effective = expand(platform_toolsets.<platform>)  −  expand(agent.disabled_toolsets)
```

`platform_toolsets.<platform>` is an **allowlist**; if it's absent or not a list the
platform falls back to its **default composite** (usually everything).
`disabled_toolsets` is a global **denylist** subtracted afterward.

## "flash but no reply" — extra causes beyond the stale session

If a full session+dedup reset (Layer 5) doesn't fix it, check in order:
1. `errors.log` / `agent.log` around that timestamp — model/provider failure on that
   specific turn.
2. Duplicate `message_id` in the inbound log (Lark double-push) → `_active_sessions`
   race.
3. A per-chat lock deadlock if the gateway is simultaneously serving a CLI
   conversation and a DM.
The root cause varies per incident — walk the checklist, don't assume one fix.

## Dual-profile / multi-app in one container

Two gateways can coexist if each has a **distinct** `HERMES_HOME` (profile dir) **and
distinct app credentials** — the platform routes events per app id, so two apps =
two independent streams, no stealing. Same app id across two profiles = they kick
each other off the single connection; `gateway_state.json` shows
`platforms.feishu.state: disconnected` while a gateway process is clearly running.

Diagnose:
```bash
ps -eo pid,ppid,etime,cmd | grep "<app> gateway" | grep -v grep
tr '\0' '\n' < /proc/<PID>/environ | grep HERMES_HOME   # which profile
cat gateway_state.json                                  # feishu.state
```
Kill the wrong PID only (e.g. the profile whose app is unpublished). Never
`pkill -f <app>` — the agent backend is a bare `<app>` process too.
