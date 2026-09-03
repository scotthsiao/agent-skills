#!/usr/bin/env bash
# Restart a background service without systemd, precisely.
#
#   1. source $APP_HOME/.env so the new process gets current values
#   2. kill ONLY processes whose command line matches $MATCH
#   3. relaunch detached, logging to $LOG
#   4. verify: new PID alive, health line present, no warning lines
#
# Configure via env (or edit the defaults below):
#   APP_HOME     dir holding .env and logs/         (default: $HOME/.hermes)
#   APP_BIN      absolute path to the executable    (required)
#   APP_ARGS     args, e.g. "gateway"               (default: "gateway")
#   MATCH        unique pkill -f pattern            (default: "$(basename "$APP_BIN") $APP_ARGS")
#   LOG          relaunch log path                  (default: $APP_HOME/logs/relaunch.log)
#   HEALTH_GREP  extended-regex expected in log     (default: "connected|listening|ready")
#   WARN_GREP    extended-regex that means trouble  (default: "Unauthorized|No env user allowlists|linger|Traceback")
#   WAIT_SECS    seconds to wait before verifying   (default: 5)
set -u

APP_HOME="${APP_HOME:-$HOME/.hermes}"
APP_BIN="${APP_BIN:?set APP_BIN to the executable path}"
APP_ARGS="${APP_ARGS:-gateway}"
MATCH="${MATCH:-$(basename "$APP_BIN") $APP_ARGS}"
LOG="${LOG:-$APP_HOME/logs/relaunch.log}"
HEALTH_GREP="${HEALTH_GREP:-connected|listening|ready}"
WARN_GREP="${WARN_GREP:-Unauthorized|No env user allowlists|linger|Traceback}"
WAIT_SECS="${WAIT_SECS:-5}"

ts() { date -u +%FT%TZ; }
mkdir -p "$(dirname "$LOG")"

# 1. load current environment
if [ -f "$APP_HOME/.env" ]; then
  echo "[$(ts)] sourcing $APP_HOME/.env"
  set -a; . "$APP_HOME/.env"; set +a
else
  echo "[$(ts)] WARN: no $APP_HOME/.env"
fi

# 2. kill only the target
echo "[$(ts)] stopping processes matching: $MATCH"
pkill -f "$MATCH" 2>/dev/null || true
sleep 2
LEFT="$(pgrep -af "$MATCH" || true)"
if [ -n "$LEFT" ]; then
  echo "[$(ts)] still alive, sending SIGKILL:"
  echo "$LEFT"
  pkill -9 -f "$MATCH" 2>/dev/null || true
  sleep 2
fi
if pgrep -f "$MATCH" >/dev/null 2>&1; then
  echo "[$(ts)] ERROR: could not kill all matches — aborting to avoid a double instance"
  pgrep -af "$MATCH"
  exit 1
fi

# 3. relaunch detached
echo "[$(ts)] starting: $APP_BIN $APP_ARGS  (APP_HOME=$APP_HOME)"
cd "$APP_HOME"
# shellcheck disable=SC2086
nohup env APP_HOME="$APP_HOME" HERMES_HOME="$APP_HOME" "$APP_BIN" $APP_ARGS \
  > "$LOG" 2>&1 &
NEW_PID=$!
disown 2>/dev/null || true

# 4. verify
sleep "$WAIT_SECS"
if ! kill -0 "$NEW_PID" 2>/dev/null; then
  echo "[$(ts)] ERROR: new process $NEW_PID exited immediately. Last log:"
  tail -n 30 "$LOG"
  exit 1
fi
echo "[$(ts)] running as PID $NEW_PID"
echo "----- tail $LOG -----"
tail -n 20 "$LOG"
echo "---------------------"

if grep -Eiq "$HEALTH_GREP" "$LOG"; then
  echo "[$(ts)] health OK (matched /$HEALTH_GREP/)"
else
  echo "[$(ts)] WARN: no health line yet (/$HEALTH_GREP/) — may still be starting"
fi
if grep -Eiq "$WARN_GREP" "$LOG"; then
  echo "[$(ts)] WARN: trouble lines present (/$WARN_GREP/):"
  grep -Ei "$WARN_GREP" "$LOG" | tail -n 5
  exit 2
fi
echo "[$(ts)] done."
