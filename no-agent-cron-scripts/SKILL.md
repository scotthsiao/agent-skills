---
name: no-agent-cron-scripts
description: >-
  Build scheduled jobs that run a plain script and deliver its output verbatim,
  without invoking the LLM. Use when setting up a recurring report, backup, health
  check, or watchdog on an agent framework that has a cron feature, when a scheduled
  task should be deterministic and free of token cost, or when a cron job that "asks
  the agent to run a script" is flaky, slow, or expensive.
version: 1.0.0
license: MIT
metadata:
  tags: [cron, automation, no-agent, scripts, reports, tokens, determinism]
  related_skills: [cron-timezone-discipline, headless-config-backup, webhook-tunnel-watchdog]
---

# No-agent cron scripts

Agent frameworks that offer scheduling usually let a job run in two ways:

- **agent mode** — the scheduler hands a *prompt* to the LLM, which reasons and calls
  tools.
- **no-agent mode** — the scheduler runs a *script* and delivers its stdout as the
  message. No model call.

For anything mechanical — a daily summary, a backup, a "is X still up" check — use
**no-agent mode**. It is deterministic, costs nothing, can't hallucinate, and can't
be derailed by a bad model day.

## When agent mode is actually needed

Only when the job genuinely requires per-run judgement: interpreting fuzzy input,
deciding *whether* to act, composing a nuanced message, chaining tools whose
arguments depend on intermediate results. If you can write the logic as a script,
write the script.

## The shape (Hermes example)

A no-agent job carries `no_agent: true`, a `script:` path, and a `deliver:` target.
The scheduler runs the script and routes its stdout to `deliver`:

```jsonc
{
  "name": "morning report",
  "schedule": { "expr": "0 1 * * 1-5" },   // 09:00 Asia/Taipei — see cron-timezone-discipline
  "no_agent": true,
  "script": "morning_report.py",            // resolved under the scripts dir
  "deliver": "line:Uxxxxxxxx"               // or "origin", "local", a chat id
}
```

Other frameworks differ in field names; the pattern is the same — *script in, stdout
out, no model*.

## Rules that keep these reliable

1. **Save the script as a file.** Never inline a here-doc or a `python -c "..."` blob
   in the job definition — it becomes unmaintainable and quoting will eventually bite
   you. One file per job in a `scripts/` dir.

2. **Run under the app's bundled interpreter**, not the system `python3`. The app's
   virtualenv already has the dependencies (`requests`, cloud SDKs). Point the job's
   `script` runner at `<app>/venv/bin/python`, or have the script re-exec itself
   under it:
   ```python
   import os, sys
   VENV = "/usr/local/lib/hermes-agent/venv/bin/python"
   if os.path.exists(VENV) and os.path.realpath(sys.executable) != os.path.realpath(VENV):
       os.execv(VENV, [VENV, os.path.abspath(__file__)] + sys.argv[1:])
   ```

3. **Silent on nothing-to-say.** If there's no news, print nothing and exit 0 — most
   schedulers suppress an empty delivery. Don't send "no changes today" every day.

4. **Silent on failure, or loud on purpose — pick one.** Decide per job: a backup
   failure *should* page you (print the error, non-zero exit); a flaky data-source
   for a nice-to-have report should just skip quietly. Don't deliver raw tracebacks
   to a chat channel by accident.

5. **Exit codes carry meaning.** `0` = success (delivered or intentionally quiet),
   non-zero = failure the scheduler should log. Keep `last_status` meaningful.

6. **Idempotent and self-contained.** The script gets no conversation context. It
   reads what it needs (config, `.env`, an API), does its thing, prints. Running it
   twice must be harmless.

7. **Fast.** These share the scheduler's tick. A watchdog that runs every minute must
   finish in well under a minute.

## Template

`scripts/report_template.py` — reads config from env, does work, prints a message or
stays silent, sets an exit code. Copy it per job.

## Delivery targets

Common `deliver` values: `origin` (reply where the job was created), `local` (just
log it), a platform + id (`line:U...`, `telegram:...`, a chat id). For a job that
must reach you even if the agent's gateway is down, prefer a direct push (`line:`,
email) over `origin`.

## Cheatsheet: is this a no-agent job?

| Job | no-agent? |
|---|---|
| nightly backup + upload | yes |
| "is the tunnel up? relaunch if not" | yes |
| daily metrics/portfolio/report from an API | yes |
| token health check | yes |
| "read today's incoming emails and tell me which need a reply" | no — needs judgement |
| "if the error rate spiked, summarize the likely cause" | no |
