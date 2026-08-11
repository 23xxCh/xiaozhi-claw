## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `23xxCh/xiaozhi-claw`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five standard labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read root `CONTEXT.md` and relevant ADRs under `docs/adr/` when they exist. See `docs/agents/domain.md`.

## Version control

- Use Git for every implementation change and keep commits scoped to one logical, verified unit.
- Before committing, confirm generated firmware, build output, databases, logs, local `.env` files, and credentials remain ignored.
- Local commits are part of the implementation workflow. Do not push, publish, rewrite shared history, or change repository visibility without explicit user authorization.
