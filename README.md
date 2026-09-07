# agent-skills

Portable, agent-agnostic skills for running an autonomous assistant **headless, in a
container, for the long haul** — cloud backup, OAuth token upkeep, a messaging
gateway, and cron discipline.

Every skill here was extracted from a real long-running [Hermes Agent](https://github.com/NousResearch/hermes)
deployment and then **generalized and scrubbed** of anything organization-specific.
They follow the [Anthropic skill format](https://code.claude.com/docs/en/skills)
(`SKILL.md` + optional `scripts/` and `references/`) and drop into Hermes,
Claude Code, or any agent that reads that format.

## Catalog

| Skill | What it's for |
|---|---|
| [`google-oauth-headless`](google-oauth-headless/) | Get a Google OAuth / Drive token on a box with no browser. The manual-PKCE and out-of-band flows, because `InstalledAppFlow` breaks across processes. |
| [`gdrive-token-refresh`](gdrive-token-refresh/) | The **weekly maintenance chore**: a Google refresh token on a "Testing" OAuth app dies every 7 days and silently kills your backup cron. Detect it, re-consent, and fix it permanently. |
| [`headless-config-backup`](headless-config-backup/) | Back up an agent's *config* (not its caches) to cloud storage on a schedule — include/exclude lists, daily/weekly tiers, local retention, a notify hook. |
| [`cron-timezone-discipline`](cron-timezone-discipline/) | Schedules are stored in UTC; humans think in local time. A converter, a documentation habit, and the DST caveat. |
| [`container-service-restart`](container-service-restart/) | Restart a long-running service (a messaging gateway) inside a container with no systemd — precise `pkill`, `.env` sourcing, health check. |
| [`no-agent-cron-scripts`](no-agent-cron-scripts/) | Cron jobs that must **not** wake the LLM: run a plain script, deliver its stdout verbatim. Deterministic and free. |
| [`lark-openapi-recipes`](lark-openapi-recipes/) | Reading Lark/Feishu wiki, docx, sheets, and IM from an agent via the OpenAPI — token minting, the crawl that works, the block-type map, the scope walls. |
| [`lark-wiki-backup`](lark-wiki-backup/) | Snapshot a Lark/Feishu wiki space to versioned local `.docx` files — delta only (new / edited / renamed / deleted), git-committed — plus an optional offsite `.tar.gz`. |
| [`messaging-gateway-bot-triage`](messaging-gateway-bot-triage/) | "The bot connected but doesn't answer." A layered decision tree: events not arriving, wrong bot, allowlist, stale session. |
| [`prompt-layer-permission-guardrail`](prompt-layer-permission-guardrail/) | One shared bot, several users, different powers — enforced in the system prompt. What it does and does not protect against. |
| [`webhook-tunnel-watchdog`](webhook-tunnel-watchdog/) | Keep an ngrok / cloudflared tunnel alive for an inbound webhook, and publish the current public URL. |

## Layout

```
<skill-name>/
├── SKILL.md        # frontmatter (name, description, version, license) + the skill body
├── scripts/        # runnable helpers referenced by SKILL.md
└── references/     # deep-dive docs the agent loads only when it needs them
```

## Using these

Point your agent's skill loader at this repo, or symlink individual skills into your
skills directory. For a multi-machine / multi-agent setup, clone the repo somewhere
neutral and symlink the skills you want:

```bash
git clone https://github.com/scotthsiao/agent-skills ~/src/agent-skills
ln -s ~/src/agent-skills/gdrive-token-refresh ~/.hermes/skills/gdrive-token-refresh
```

## Contributing

These grow by extraction: you hit a wall on your own agent, you solve it, you lift
the reusable core out. See [CONTRIBUTING.md](CONTRIBUTING.md) — the important part is
the **scrub** step.

## License

MIT. See [LICENSE](LICENSE).
