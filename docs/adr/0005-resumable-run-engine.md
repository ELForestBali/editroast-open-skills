# ADR-0005: Revision-bound resumable run engine

- Status: accepted for PR-05
- Scope: ER-014
- Date: 2026-10-28

## Context

The v1 workflow records completed chunks in `state.json`, but durable manuscript projects need a shared checkpoint contract for later roast, proofreading, translation, and production orchestration. A process can stop after a provider usage limit, a local crash, or an author decision. Restarting must not repeat verified completed work or report 100% from status flags alone.

## Decision

Extend the canonical project SQLite schema with ordered `run_units` and append-only `run_events`. Bind each run to one immutable revision and a deterministic plan hash covering its content-map hash, run type, scope, and stable chapter or paragraph IDs. Preserve the v1 JSON workflow and its public CLI unchanged.

Claim one pending unit at a time and issue a unique attempt token. Accept completion only from the active attempt and only when a non-empty result file is inside the project. Store its project-relative path and SHA-256. Treat an identical completion retry as idempotent; reject stale tokens and changed results.

Represent deliberate stops as `paused`, `blocked_by_usage_limit`, or `needs_author_decision`. On resume, requeue abandoned in-progress and failed units while retaining verified completed units. If a completed result disappears or changes, lower verified coverage, expose `integrity_error`, and allow only that damaged unit to be retried. Refuse resume when the source no longer matches the plan.

Calculate progress from verified result files, not only database status. Include managed runs in project integrity verification so a missing or modified result prevents a successful project check and false 100% coverage.

## Consequences

- Killing a worker midway and reopening the project resumes from the first unfinished unit without repeating verified work.
- Duplicate completion calls are safe, while a prior session cannot complete a newly claimed retry.
- Usage-limit checkpoints do not infer reset time, available tokens, or permission to buy credits or switch providers.
- Result integrity is verifiable, but report quality and editorial correctness remain human/model concerns.
- Schema-v4 projects migrate in place to schema v5; legacy `runs` rows without unit plans remain preserved and are not treated as ER-014 managed runs.
