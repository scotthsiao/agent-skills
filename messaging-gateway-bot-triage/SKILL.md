---
name: messaging-gateway-bot-triage
description: >-
  Diagnose why a messaging bot connected to an agent gateway (Lark/Feishu, Telegram,
  Discord, Slack, WhatsApp) doesn't respond, or replies with the platform's own
  canned/default message. Use when the gateway says "connected" but the bot is
  silent, when a specific user gets no reply while others work, when a bot shows a
  typing indicator then nothing, when a permission/tool restriction "isn't taking
  effect", or right after wiring up a new bot.
version: 1.0.0
license: MIT
metadata:
  tags: [gateway, messaging, bot, troubleshooting, lark, feishu, telegram, discord, slack, websocket, allowlist]
  related_skills: [container-service-restart, prompt-layer-permission-guardrail, lark-openapi-recipes]
---

# Messaging gateway bot triage

"I connected a bot to the gateway and it isn't working." Work the layers **in
order, cheapest first** — each must pass before the next matters. Don't skip ahead,
and don't keep restarting the gateway: for most of these, a restart fixes nothing.

Framing is platform-agnostic; Lark/Feishu specifics are called out because that's
where the sharp edges are.

---

## Layer 0 — Is the gateway process even running?

```bash
<app> gateway status          # want: "running (PID …)"
pgrep -af "<app> gateway"
```

Not running → start it (see [`container-service-restart`](../container-service-restart/)
for the no-systemd case). In a container, the framework's own `gateway restart` often
fails with a linger error — restart manually.

## Layer 1 — Did the platform adapter connect?

```bash
grep -iE "connect|websocket|<platform>" ~/.hermes/logs/gateway.log | tail -20
```

Want `✓ <platform> connected` / `Connected in websocket mode`.

**A successful connect only proves the credentials are valid.** It does **not** mean
messages are being delivered. This is the trap that eats hours — people see
"connected" and assume the pipe works. It doesn't. Go to Layer 2.

## Layer 2 — Are inbound message events actually arriving?

Send the bot a DM, then:

```bash
grep -iE "inbound|message.receive|received|im.message|queued" ~/.hermes/logs/gateway.log | tail
```

- **Lines appear** (`Inbound dm message received … text=…`) → events are arriving,
  problem is downstream → Layer 3.
- **Nothing at all** — not even a "queued" or "dropped" line → the platform never
  delivered the event. Problem is upstream (platform console), one of:

  1. **Wrong bot / Helpdesk trap** (Lark). If the bot auto-replies with *"Hello, how
     can I help you?"* / *"No relevant answers, Contact Agent"*, you're talking to
     Lark's built-in **Helpdesk (服务台)**, not your app bot. Your real app bot is
     **silent** until the agent answers — any canned auto-reply means wrong endpoint.
     Fix: find the real bot — search → **Bots** tab (not 服务台), or add the app to a
     group and @-mention it there (most reliable).
  2. **Connection mode is webhook, not long-connection.** Console → Events &
     Callbacks → must be **Long Connection (WebSocket)**, not "send events to a URL".
     If it's URL mode, your websocket gateway gets nothing.
  3. **Event not subscribed** — subscribe the inbound-message event
     (`im.message.receive_v1` on Lark).
  4. **App version not published.** On Lark/Feishu, event and permission changes do
     **not** take effect until a new app version is **published and approved**.
     "已發佈/Published" in the list can still mean "under review" — check the online
     version actually lists the event.
  5. **App ID mismatch.** With more than one app, `FEISHU_APP_ID` in `.env` must be
     the same app whose console you configured **and** the same bot you're DMing. A
     valid-but-wrong app_id connects fine and receives zero events — identical
     symptom to "not subscribed". Cross-check `.env` against the console.
  6. **Two gateways, one app.** The platform delivers events to **one** connection
     per app. A second gateway (another machine, or a second profile with the same
     credentials) steals the stream. Kill duplicates — see
     [`container-service-restart`](../container-service-restart/).

## Layer 3 — Is the sender blocked by the allowlist?

Events arrive but get dropped before the agent. Log:

> `Unauthorized user: <hash> on <platform>`
> `No env user allowlists configured. … will deny unknown senders`

- **Empty allowlist + `ALLOW_ALL_USERS=false` → everyone is denied**, including you.
- **Quick unblock:** `FEISHU_ALLOW_ALL_USERS=true` (per-platform var; the warning
  names the global `GATEWAY_ALLOW_ALL_USERS`, but the platform one is enough).
- **Production allowlist:** `FEISHU_ALLOWED_USERS=<ids>`. Two id spaces exist and
  which one the auth layer compares against has **changed between gateway versions** —
  the safe move is to **put both per member**: the `open_id` (`ou_…`, from
  `sender=user:ou_…` in the inbound log) **and** the tenant `user_id` (the short hash
  in the `Unauthorized user: <hash>` line). Comma-separated, no spaces.
- **Capture a new member's ids (3 steps):** they DM once → `grep "Unauthorized user"`
  for the hash → grab their `open_id` from the next inbound line or via
  `lark-cli contact +search-user` → add both, restart.
- An `Unauthorized user: <hash>` line for a hash you don't recognize is just *a
  different, non-allowlisted person* being correctly blocked — not the member you're
  debugging.

After any `.env` change: **restart the gateway, and make sure the restart sources
`.env`** — see [`container-service-restart`](../container-service-restart/). A plain
`pkill` + relaunch may keep the *old* env and you'll chase ghosts.

## Layer 4 — Bot responds, but a tool/permission restriction isn't applied

Symptom: you set `disabled_toolsets` / `platform_toolsets` to block `cronjob` /
`terminal` / `web` on the bot, but it still does them.

**Root cause (silent):** the list was written as a **quoted YAML string**, not a
list:

```yaml
# WRONG — parses as str, filter silently no-ops, full tools restored
disabled_toolsets: '[''cronjob'', ''terminal'']'
# RIGHT
disabled_toolsets: ['cronjob', 'terminal', 'delegation', 'browser', 'kanban', 'code_execution', 'skills', 'web', 'computer_use']
```

Diagnose without a round-trip:

```python
import yaml
raw = yaml.safe_load(open('config.yaml'))
print(type(raw['agent']['disabled_toolsets']).__name__)   # want 'list', 'str' == bug
```

Fix from a terminal (`config.yaml` is protected — the agent's write tools refuse it):
`ast.literal_eval` the string into a real list, `yaml.safe_dump` back, back up first,
restart. Full detail + a verify probe in
[`references/deep-dive.md`](references/deep-dive.md). Related:
[`prompt-layer-permission-guardrail`](../prompt-layer-permission-guardrail/) for why
you want config-level restriction, not just a prompt rule.

Two more from this layer:
- `disabled_toolsets` only takes whole **toolset names** — you cannot ban a single
  tool like `write_file`. For "read but not write", keep the toolset and enforce it
  in the skill prompt.
- `code_execution` is a `terminal` backdoor (it can spawn a shell) — disable both.
- Don't disable `file` if a skill reads a local knowledge path — you'll sever its
  source.

## Layer 5 — Inbound arrives, a "typing"/reaction flashes, but no reply

A specific user (often not the admin) DMs; the log shows `Inbound … received` and
`Flushing text batch …` but **no** `response ready` / `Sending response` for that
chat. The message "disappears".

**Cause:** a per-chat session guard (`_active_sessions`) still holds that chat's key
from a prior turn that crashed or a stale resumed session, so every new message is
queued behind an in-flight turn that will never complete.

**Fix — full state reset for that platform** (keeps other platforms):

```python
import json, os, datetime
stamp = datetime.datetime.now().strftime('%H%M%S')
p = 'sessions/sessions.json'
d = json.load(open(p)); open(f'{p}.bak_{stamp}', 'w').write(json.dumps(d))
json.dump({k: v for k, v in d.items() if 'feishu' not in str(k).lower()},
          open(p, 'w'), ensure_ascii=False, indent=2)
dd = 'feishu_seen_message_ids.json'      # persistent dedup state
if os.path.exists(dd): os.rename(dd, f'{dd}.bak_{stamp}')
```

Then restart and have the user send one fresh DM. Clear **all** of that platform's
session keys plus the dedup file in one pass — a residual entry re-traps the user.
Also check whether the platform pushed the **same `message_id` twice** (Lark does
this occasionally) — a duplicate event worsens the lock race.

Note: this is the one place a **restart genuinely helps** (it clears in-memory
session state) — distinct from Layer 2, where restarting is useless.

---

## Verify a fix end to end

1. `<app> gateway status` — note the new PID.
2. Fresh `✓ <platform> connected` in the log, timestamp after the restart.
3. User sends one DM.
4. Re-grep Layer 2 — a real inbound event now appears.
5. Bot replies with agent output, not a platform default → done.

## Protected files

`.env` and `config.yaml` are commonly protected — the agent's `write_file` / `patch`
tools refuse them. Edit from a terminal, back up first
(`cp .env .env.bak.$(date +%s)`), restart to apply.
