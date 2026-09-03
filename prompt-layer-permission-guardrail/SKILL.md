---
name: prompt-layer-permission-guardrail
description: >-
  Run one shared bot for several people who should have different powers — an admin
  who can do anything, others restricted to a narrow read-only interaction — enforced
  through the system prompt keyed on the sender's id. Use when setting up a team
  knowledge-base bot, when asked to "let members ask but not change things", or when
  deciding between a prompt rule, config-level tool isolation, and separate bots.
version: 1.0.0
license: MIT
metadata:
  tags: [permissions, multi-user, system-prompt, guardrail, bot, security, soft-isolation]
  related_skills: [messaging-gateway-bot-triage]
---

# Prompt-layer permission guardrail

You have one bot on a shared channel. The owner should be able to run commands and
change config; everyone else should only be able to ask questions. The cheapest way
to express that is a block in the system prompt that branches on **who sent the
message**.

This works — for a trusted internal team, with a capable model — but understand
exactly what it does and doesn't protect against before you rely on it.

## The pattern

Add a section to the bot's system prompt / persona file:

```markdown
## Permission guardrail (multi-user)

Several people use this bot. Apply powers by the sender's id:

- **<Owner name> (id `<owner-id>`)**: full access — run terminal commands, edit
  config/skills/schedules, read and write any file, message others.
- **Everyone else**: <narrow role> only:
  - ✅ <the allowed interaction, e.g. ask questions against the wiki (read-only)>
  - ❌ Never: write or modify any file; run shell/terminal commands; change bot
    config; install or edit skills; add or remove scheduled jobs; message anyone.
  - If a non-owner asks for any of the above, decline politely: "That needs admin
    access — please contact <Owner>." and do not do it.
  - Identify the sender from the message context's sender id. If you cannot
    determine it, assume the **least** privileged role.
```

Keep the id list short and in one place. If the interaction has its own procedure
(e.g. "how to answer from the wiki"), put that in a separate skill the guardrail
points to, so this block stays about *authorization* only.

## What this protects against

- A cooperative model + a well-behaved user who simply shouldn't be *offered* admin
  actions.
- Accidental damage ("can you delete that file for me?") from someone who isn't the
  owner.
- Keeping the interaction on-rails (members ask the wiki, they don't wander into the
  filesystem).

## What it does NOT protect against

- **A weak or adversarial model.** A small model will cheerfully call `cronjob` or
  `terminal` when a member asks nicely. The guardrail is a *request*, not a
  boundary.
- **Prompt injection** in the content the bot reads.
- **Anyone determined.** It's social, not structural.

## Layer it — the real boundary lives lower

Order of enforcement, outermost first:

1. **Transport allowlist** (gateway `.env`): a sender not on the allowlist never
   reaches the agent at all — the guardrail never runs for them. Use this to decide
   *who can talk to the bot*. See
   [`messaging-gateway-bot-triage`](../messaging-gateway-bot-triage/) Layer 3.
2. **Config-level tool isolation** (`disabled_toolsets` / `platform_toolsets`): the
   model **cannot call a tool that isn't in its toolset**. This is the hard control
   for *what the bot can do*. For a read-only knowledge bot, disable
   `cronjob, terminal, code_execution, delegation, browser, kanban, skills, web,
   computer_use` on that platform — **keep `file`** if a skill reads a local
   knowledge path. Note it must be a real YAML list, not a quoted string (a classic
   silent failure — see the triage skill's deep-dive).
   - `code_execution` can spawn a shell → disable it alongside `terminal`.
   - Toolset granularity only: you can't ban `write_file` alone. "Read but not
     write" for one path is exactly where the prompt guardrail earns its place —
     `file` stays enabled, the prompt forbids writes.
3. **The prompt guardrail** (this skill): the last, softest layer — good for tone,
   for declining gracefully, and for the fine-grained rules the config can't
   express.
4. **Separate bots** (two apps, two gateways/profiles): true isolation. Worth it
   when the users aren't all trusted, or when the console friction of publishing a
   second app is less than the risk. Overkill for a small trusted team.

## Rule of thumb

> Decide **who** with the transport allowlist. Decide **what** with config tool
> isolation. Use the prompt guardrail for the nuance and the manners. Reach for
> separate bots only when trust isn't uniform.

Say so explicitly in the guardrail text itself — a one-line footnote that "this is a
prompt-layer soft isolation for a trusted internal team; use a dual-bot architecture
for hard isolation" keeps the next person from over-trusting it.
