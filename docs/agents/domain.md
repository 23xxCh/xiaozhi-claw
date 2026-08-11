# Domain docs

This is a single-context repository. Engineering skills must use the repository's domain language and architectural decisions when exploring or changing code.

## Before exploring

- Read `CONTEXT.md` at the repository root.
- Read ADRs under `docs/adr/` that affect the area being changed.
- If either location is absent, proceed silently; create domain documentation only when a real term or decision has been resolved.

## Vocabulary

Use terms exactly as defined in `CONTEXT.md` in issue titles, implementation plans, tests, documentation, and user-facing product text. Avoid synonyms that the glossary explicitly rejects.

If a required concept is missing, first decide whether it is unnecessary new terminology or a genuine domain gap. Record genuine gaps through the domain-documentation workflow.

## Architectural decisions

If proposed work contradicts an existing ADR, identify the conflict explicitly instead of silently overriding it. New durable decisions belong under `docs/adr/`.
