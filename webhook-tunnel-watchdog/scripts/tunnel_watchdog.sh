#!/usr/bin/env bash
# Keep an inbound webhook tunnel alive and publish its public URL.
# Run every minute from cron (a no-agent job).
#
# Env:
#   TUNNEL          ngrok | cloudflared        (default: ngrok)
#   TARGET_PORT     local port to expose       (default: 8646)
#   URL_FILE        where to write the URL     (default: /tmp/tunnel-url.txt)
#   LOG            relaunch log                (default: /tmp/tunnel-watchdog.log)
#   ON_URL_CHANGE   cmd run with the new URL as $1 when it changes (optional)
#   NGROK_BIN       (default: ngrok)   CF_BIN (default: cloudflared)
#   CF_TUNNEL_NAME  cloudflared named tunnel (recommended: stable URL)
#   CF_HOSTNAME     the hostname routed to CF_TUNNEL_NAME (used as the URL)
set -u

TUNNEL="${TUNNEL:-ngrok}"
TARGET_PORT="${TARGET_PORT:-8646}"
URL_FILE="${URL_FILE:-/tmp/tunnel-url.txt}"
LOG="${LOG:-/tmp/tunnel-watchdog.log}"
ON_URL_CHANGE="${ON_URL_CHANGE:-}"
NGROK_BIN="${NGROK_BIN:-ngrok}"
CF_BIN="${CF_BIN:-cloudflared}"
CF_TUNNEL_NAME="${CF_TUNNEL_NAME:-}"
CF_HOSTNAME="${CF_HOSTNAME:-}"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

prev_url=""
[ -f "$URL_FILE" ] && prev_url="$(cat "$URL_FILE" 2>/dev/null || true)"

publish() {
  local url="$1"
  [ -z "$url" ] && return 0
  printf '%s\n' "$url" > "$URL_FILE"
  if [ "$url" != "$prev_url" ]; then
    log "url changed: $prev_url -> $url"
    [ -n "$ON_URL_CHANGE" ] && sh -c "$ON_URL_CHANGE \"\$1\"" _ "$url" >> "$LOG" 2>&1 || true
  fi
}

ngrok_url() {
  curl -s -m 4 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json
try:
    t=json.load(sys.stdin)['tunnels']
    print(next((x['public_url'] for x in t if x['public_url'].startswith('https')), t[0]['public_url']) if t else '')
except Exception:
    print('')" 2>/dev/null
}

case "$TUNNEL" in
  ngrok)
    if pgrep -f "$NGROK_BIN http.*$TARGET_PORT" >/dev/null 2>&1; then
      publish "$(ngrok_url)"
      exit 0
    fi
    log "ngrok down — relaunching on :$TARGET_PORT"
    nohup "$NGROK_BIN" http "$TARGET_PORT" --log=stdout >> /tmp/ngrok.out 2>&1 &
    disown 2>/dev/null || true
    sleep 6
    url="$(ngrok_url)"
    [ -z "$url" ] && { sleep 5; url="$(ngrok_url)"; }
    publish "$url"
    log "relaunched, url=${url:-<none yet>}"
    ;;

  cloudflared)
    if pgrep -f "$CF_BIN tunnel .*run" >/dev/null 2>&1; then
      [ -n "$CF_HOSTNAME" ] && publish "https://$CF_HOSTNAME"
      exit 0
    fi
    log "cloudflared down — relaunching"
    if [ -n "$CF_TUNNEL_NAME" ]; then
      nohup "$CF_BIN" tunnel run "$CF_TUNNEL_NAME" >> /tmp/cloudflared.out 2>&1 &
    else
      nohup "$CF_BIN" tunnel --url "http://localhost:$TARGET_PORT" \
        >> /tmp/cloudflared.out 2>&1 &
    fi
    disown 2>/dev/null || true
    sleep 8
    if [ -n "$CF_HOSTNAME" ]; then
      publish "https://$CF_HOSTNAME"
    else
      url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.out | tail -1)"
      publish "$url"
    fi
    log "relaunched cloudflared"
    ;;

  *)
    log "unknown TUNNEL=$TUNNEL"
    exit 1
    ;;
esac
