# ADR-0007: Audited Editorial Board decisions and one comparative re-check

- Status: accepted for ER-021 release candidate
- Scope: ER-021 S4
- Date: 2026-10-29

## Context

ER-021 S1-S3 validate three independent editorial lenses and an explicit Chief
Sterling adjudication, but the result was stateless. Authors need inspectable
approve/reject/defer decisions, safe application of exact approved changes,
dependency/conflict handling, rollback, and a concise check of the revised
book. Duplicating ER-013 apply or ER-014 checkpoints would create incompatible
state and weaken project portability.

## Decision

Add schema-v7 board review, group, event and re-check tables beside the existing
canonical state. Persist the validated adjudication with a canonical SHA-256
and create one normal `author_decisions` row per canonical group. Keep all
decision changes append-only.

Do not create ER-013 corrections for rejected or deferred groups. A first
approval of a revision group must include exact paragraph-local changes and
explicit author confirmation. Validate dependencies, conflicts, source spans
and semantic-group confirmation, then write approved changes into the existing
ER-013 correction/event tables. Delegate preview, apply and rollback to ER-013.

For a reconciled descendant revision, create one chapter-scoped ER-014 run with
the type `editorial-board-recheck`. Validate every improved, unresolved or new
assessment against exact revised-source evidence. Finalize one comparative
report and verdict; do not launch three new persona runs by default.

Include board and re-check IDs in schema-v7 bundle manifests while continuing
to accept schema-v6 bundle manifests without those fields. Rebuild author JSON
or Markdown from SQLite so reports do not become a second authority.

## Consequences

- Author consent, board reasoning, ER-013 correction IDs and revision outcomes
  remain inspectable and portable.
- Dependency/conflict failures stop before an apply preview; ER-013 retains the
  final source-integrity and renderer authority.
- Rollback remains an immutable successor revision and never deletes history.
- Re-check can pause/resume through the existing usage-limit mechanism and
  produces one author-facing comparison.
- Deterministic checks establish evidence and state integrity, not the quality
  of literary judgments. Independent EN/RU expert scoring remains separate.
