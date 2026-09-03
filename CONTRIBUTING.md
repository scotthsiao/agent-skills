# Contributing

## The model: skills grow by extraction

You run an agent. Over weeks it accumulates hard-won knowledge — a gotcha appended to
a `SKILL.md` after a real session, a script that finally worked on the fifth try.
That knowledge is stuck on one machine.

This repo is where the **reusable core** of that knowledge comes to live so other
people (and your other agents) get it for free. A contribution is usually:

1. **A new skill** — you solved a class of problem that isn't covered here.
2. **A new lesson** — an existing skill was almost right but missed a case you hit.

## Workflow

1. Fork / branch: `git checkout -b add/<skill-name>` or `lessons/<skill-name>-<date>`.
2. Add or edit the skill.
3. Open a PR. Describe the real situation that produced the knowledge.

Keep volatile, dated knowledge in `references/lessons.md` (append-only, one dated
entry per lesson). Keep the stable procedure in `SKILL.md`. Append-only lesson files
almost never conflict, so lesson PRs can move fast; `SKILL.md` changes get a real read.

## The scrub (do this before every PR)

Everything here started as an internal skill. Before it can be public it must lose:

- **Identifiers** — user ids, open_ids, account hashes, chat ids, device ids, email
  addresses, phone numbers.
- **Hostnames and endpoints** — internal domains, private IPs (RFC1918), database
  names, bucket/folder names that reveal an org.
- **Org names** — company, team, product, project names. Replace with a role
  (`the operator`, `the team`, `<your-app>`).
- **Absolute paths that leak identity** — `/root/.hermes/...` is fine as an example;
  `/root/llm-wiki/raw/AcmeCorp QA/...` is not.
- **Secrets** — obviously. Never a real `client_secret`, token, or `.env` value.
  Reference the *path*, never the contents.
- **Portfolio / financial / personal specifics** — holdings, balances, names.

A skill in this repo should describe a **mechanism**, not a deployment. If you can't
explain it without naming your employer, it isn't ready.

Rule of thumb: `grep -riE '<yourcompany>|<yourteam>|ou_[0-9a-f]{10}|(^|[^0-9])10\.|192\.168\.|/raw/' .`
over your diff before you push.

## Style

- Follow the frontmatter shape the existing skills use (`name`, `description`,
  `version`, `license`, `metadata.tags`).
- Write the `description` so an agent can decide *from that line alone* whether the
  skill is relevant — include the trigger phrases a user would say.
- Prefer a runnable `scripts/` helper over a copy-paste code block in prose.
- One skill = one coherent problem. If it needs an "and", split it.
