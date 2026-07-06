# AGENTS.md

Guidance for AI coding agents working in this repo. See `docs/agents/` for the
per-skill configuration referenced below.

## Agent skills

### Issue tracker

Issues and PRDs live as **GitHub issues** on `semantic-craft/paper-muse`, driven
through the `gh` CLI. External PRs are **not** a triage surface — `/triage`
processes issues only. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use their default label strings
(`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`).
See `docs/agents/triage-labels.md`.

### Domain docs

**Single-context** layout — one `CONTEXT.md` + `docs/adr/` at the repo root,
created lazily by `/domain-modeling`. See `docs/agents/domain.md`.
