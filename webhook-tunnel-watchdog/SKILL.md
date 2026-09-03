---
name: webhook-tunnel-watchdog
description: >-
  Keep an inbound-webhook tunnel (ngrok or cloudflared) alive on a headless box and
  publish its current public URL. Use when a platform needs to reach a bot/webhook
  running behind NAT or in a container, when a messaging integration keeps breaking
  because the tunnel URL changed, or when setting up a per-minute cron to relaunch a
  dead tunnel.
version: 1.0.0
license: MIT
metadata:
  tags: [ngrok, cloudflared, tunnel, webhook, watchdog, cron, headless]
  related_skills: [no-agent-cron-scripts, messaging-gateway-bot-triage]
---

# Webhook tunnel watchdog

Some platforms can only deliver events by **calling a public URL** (a webhook) — they
don't offer an outbound long-connection. On a headless box or in a container with no
public IP, you bridge that with a tunnel (`ngrok`, `cloudflared`). Tunnels die:
process crash, network blip, ngrok's session timeout. This skill keeps one up and
keeps the rest of the system pointed at the right URL.

> If your platform supports a **WebSocket / long-connection** mode (Lark/Feishu,
> Telegram, Discord all do), use that instead — no tunnel, no watchdog, no
> changing URL. This skill is only for webhook-only integrations, or where you
> specifically want an inbound HTTP endpoint.

## What the watchdog does

Run `scripts/tunnel_watchdog.sh` from cron **every minute** (a
[`no-agent`](../no-agent-cron-scripts/) job):

1. If the tunnel process is alive → refresh the public-URL file and exit.
2. If it's dead → relaunch it detached, wait for it to come up, capture the new
   public URL.
3. Write the URL to `$URL_FILE` (default `/tmp/tunnel-url.txt`).
4. Optionally run `$ON_URL_CHANGE` with the new URL when it differs from last time —
   use this to re-register the webhook with the platform.

```bash
TUNNEL=ngrok \
TARGET_PORT=8646 \
URL_FILE=/tmp/tunnel-url.txt \
ON_URL_CHANGE='my-tool set-webhook' \
  bash scripts/tunnel_watchdog.sh
```

## ngrok vs cloudflared

| | ngrok (free) | cloudflared (free, named tunnel) |
|---|---|---|
| URL stability | **changes every restart** unless you have a reserved domain (paid) | **stable** if you use a named tunnel + DNS route |
| local API for URL | `http://127.0.0.1:4040/api/tunnels` | parse startup logs, or use a fixed hostname |
| setup | `ngrok config add-authtoken …` | `cloudflared tunnel create`, `tunnel route dns`, a config file |

**If you can, use a `cloudflared` named tunnel** — the public hostname never changes,
so you register the webhook once and the watchdog only needs to keep the process
alive. With free ngrok, the URL churns and you *must* wire `ON_URL_CHANGE` or the
integration breaks on every relaunch.

## The URL-changed problem

Free ngrok hands you a new `https://<random>.ngrok-free.app` on every start. Anything
that stored the old URL — the platform's webhook config, another service — is now
pointing at nothing. Options, best first:

1. Reserved domain (ngrok paid) or a `cloudflared` named tunnel → URL never changes.
2. `ON_URL_CHANGE` hook re-registers the webhook automatically on each relaunch.
3. Publish the URL to a file/endpoint that consumers read live instead of caching.

## Cron wiring

```
* * * * *   tunnel_watchdog.sh        # keep it alive, ~instant when healthy
```

It must finish in well under a minute — the healthy path is one `pgrep` + one
`curl`, so that's fine. Log relaunches (not every tick) to a file you can review.

## Verify

```bash
cat /tmp/tunnel-url.txt                       # current public URL
curl -sS "$(cat /tmp/tunnel-url.txt)/health"  # your webhook responds through it
# kill the tunnel by hand, wait 60s, check it came back and the URL file updated
```
