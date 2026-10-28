# Changelog

## 2.0.0-alpha.6 — unreleased

- Added ER-015 portable `.editroast` project bundles containing immutable revisions, canonical SQLite state, approvals, findings, applications, and referenced run results.
- Added manifest SHA-256 verification, archive traversal/size/ratio defenses, format/schema compatibility gates, sanitized service metadata, credential/private-path rejection, and atomic import without overwriting an existing project.
- Added `bundle export`, `bundle inspect`, and `bundle import` commands plus clean-install round-trip tests that preserve revision and finding IDs and resumable run state.

## 2.0.0-alpha.5 — unreleased

- Added ER-014 schema-v5 resumable runs with immutable revision-bound plans, chapter or paragraph work units, attempt tokens, and append-only checkpoint history.
- Added idempotent completion, explicit usage-limit and author-decision checkpoints, crash recovery that requeues only unfinished work, and project-local SHA-256-verified results.
- Added verified coverage that refuses false 100% completion when a result is missing, empty, outside the project, changed, or no longer bound to the source plan.

## 2.0.0-alpha.4 — unreleased

- Added ER-013 correction proposals with append-only approve, reject, defer, batch, and semantic-group decision history.
- Added deterministic apply previews, exact plan hashes, source-integrity and stale-revision gates, overlap and ambiguity blocking, and explicit author confirmation.
- Added conservative TXT/Markdown/DOCX application to a new immutable revision, verified before/after spans, DOCX formatting-collision refusal, and previewed non-destructive rollback revisions.

## 2.0.0-alpha.3 — unreleased

- Added ER-012 structural revision diffs for chapter and paragraph moves, changes, splits, merges, additions, removals, and repeated-fragment ambiguity.
- Added ranked reconciliation candidates with confidence and lexical risk signals for names, numbers, and negations; these signals do not claim to infer meaning or authorial intent.
- Added append-only manual reconciliation history, schema-v3 backfill for PR-02 projects, diff integrity checks, and offline CLI commands for inspection and resolution.

## 2.0.0-alpha.2 — unreleased

- Added ER-010 durable SQLite manuscript projects, schema migrations, immutable revision snapshots, integrity verification, revision-linked findings, and append-only author-decision history.
- Added ER-011 safe DOCX/TXT/Markdown ingestion with structured chapters, paragraphs, formatting runs, warnings, archive safety limits, and conservative cross-revision paragraph identity.
- Added the offline `editroast.py` project/manuscript CLI while preserving the v1 workflow and preview-ledger commands.

## 2.0.0-alpha.1 — unreleased

- Added a four-skill source layout with Literary Translation and Book Production clearly labelled BETA.
- Established `src/editroast_core/` as the single canonical runtime and retained the v1 `scripts/manuscript_workflow.py` CLI path through a compatibility entry point.
- Added deterministic standalone bundles, SHA-256 manifests, isolated Codex/Claude installation smoke tests, and cross-platform CI.
- Documented historical provenance, exact RU/EN/ES persona localization, migration, privacy, security, and license assumptions.

## 1.0.0 — 2026-10-24

- Added Full Manuscript Roast and Full Manuscript Proofreading skills.
- Added local TXT, Markdown, and DOCX preparation with stable chunk IDs.
- Added resumable progress tracking and evidence validation.
- Added Codex, Claude Code, and manual-chat installation guides.
- Added a synthetic three-chapter manuscript and sample reports.
- Added versioned individual and complete-pack ZIP builds.

