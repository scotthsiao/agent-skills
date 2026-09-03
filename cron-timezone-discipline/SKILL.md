---
name: cron-timezone-discipline
description: >-
  Get cron schedules right when the scheduler runs in UTC but the operator thinks in
  a local timezone. Use when adding or editing a scheduled job, when a job fired at
  the wrong hour, when documenting what runs when, when converting "every day at
  09:00 Taipei" into a cron expression, or when a job drifted by an hour after a
  daylight-saving change.
version: 1.0.0
license: MIT
metadata:
  tags: [cron, timezone, utc, scheduling, dst, automation]
  related_skills: [no-agent-cron-scripts, headless-config-backup]
---

# Cron timezone discipline

Most schedulers — system `cron`, and the cron features of agent frameworks — store
and fire expressions in **UTC**. The operator lives in a local timezone. Every
"why did it run at the wrong time" bug is this gap.

## The rule

- **Store** every schedule as a UTC cron expression.
- **Communicate and document** every schedule in the operator's local time.
- Any table, status message, or hand-off that lists jobs shows **both**.

## Convert

```bash
# local wall-clock -> UTC cron expression
python3 scripts/tz.py to-cron --tz Asia/Taipei --at 09:00 --days mon-fri
#   -> 0 1 * * 1-5      (# 09:00 Asia/Taipei = 01:00 UTC)

python3 scripts/tz.py to-cron --tz Asia/Taipei --at 02:00 --days daily
#   -> 0 18 * * *       (# 02:00 Asia/Taipei = 18:00 UTC, previous day)

# explain an existing UTC expression in local time
python3 scripts/tz.py explain --tz Asia/Taipei "0 19 * * 6"
#   -> Saturday 19:00 UTC  =  Sunday 03:00 Asia/Taipei
```

`--tz` accepts any IANA name; defaults to `$CRON_TZ` then the host zone.

## Document jobs like this

| Job | Local (Asia/Taipei) | UTC cron | Notes |
|---|---|---|---|
| config backup (daily) | 02:00 daily | `0 18 * * *` | fires previous UTC day |
| config backup (weekly) | Sat 03:00 | `0 19 * * 6` | UTC weekday is still Sat here |
| token health check | Sat 09:00 | `0 1 * * 6` | one day before nothing — runs before Sun jobs |
| morning report | weekdays 09:00 | `0 1 * * 1-5` | |

Keep this table in the repo / runbook and update it in the same commit that changes a
schedule.

## Gotchas

- **The weekday can shift.** `02:00 Mon Asia/Taipei` is `18:00 Sun UTC` — the cron
  `dow` field must be `0` (Sun), not `1`. `tz.py` handles this; hand-editing does
  not.
- **DST drift.** A fixed UTC expression is *stable in UTC*, so for any operator whose
  zone observes daylight saving it moves by an hour twice a year. If a job must hit a
  local wall-clock time year-round, either (a) accept the ±1h, (b) use a scheduler
  that supports a `TZ`/`CRON_TZ` per job and set it to the IANA zone, or (c) run the
  job hourly around the target and have the script no-op unless the local time
  matches.
- **`CRON_TZ`.** System crontabs support a `CRON_TZ=Asia/Taipei` line that makes
  following entries local. Many embedded/agent cron implementations **ignore it** —
  verify before relying on it; if unsure, store UTC.
- **No `update` subcommand.** Some cron CLIs (including some agent frameworks) only
  let you *edit* a schedule (`... edit <id> --schedule '<expr>'`), not "update" it,
  and silently no-op an unknown verb. After any change, **read the job back** and
  confirm the stored expression and the computed next-run time.
- **Verify against next-run, not the expression.** After creating a job, check its
  reported `next_run_at` (in UTC) and convert — a correct-looking expression in the
  wrong field still parses.

## Quick checklist when a job fires at the wrong time

1. Read the stored expression. Is it UTC? (It almost certainly is.)
2. `python3 scripts/tz.py explain --tz <your zone> "<expr>"` — does the local time
   match intent?
3. If off by a whole number of hours → timezone conversion error (or DST). Recompute
   with `to-cron`.
4. If off by minutes → the scheduler's tick granularity or a slow job queue, not a
   timezone issue.
