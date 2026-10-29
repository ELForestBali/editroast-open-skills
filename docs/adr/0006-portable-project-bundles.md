# ADR-0006: Sanitized portable project bundles

- Status: accepted for PR-06
- Scope: ER-015
- Date: 2026-10-28

## Context

A durable project currently depends on one local directory. Authors need a recoverable transfer artifact that preserves immutable revisions, stable IDs, findings, approvals, safe-apply history, and resumable run checkpoints on another machine. Copying a live SQLite directory can omit WAL state, include staging debris, leak machine paths or credentials in service metadata, and provide no proof that files were copied intact.

## Decision

Define `.editroast` as a versioned ZIP container with a JSON manifest, a SQLite backup made through SQLite's backup API, a generated portable project locator, all referenced immutable revision snapshots, and completed run-result files. Exclude WAL/SHM, staging, orphaned revisions, and unreferenced work products.

Sanitize known service JSON metadata recursively. Redact sensitive-key values and absolute user paths, then vacuum the copied database so replaced values do not remain in free pages. Audit every final payload, including nested DOCX members, for credential-like strings and private absolute paths. Refuse export rather than alter immutable manuscript bytes or silently ship a detected leak.

Record format/schema versions, project identity, revision/finding/decision/application/run IDs, schema-v7 editorial review/re-check IDs, and size/SHA-256 for every payload. Validate archive path safety, duplicates, encryption, symlinks, member and total sizes, compression ratio, exact manifest membership, hashes, supported versions, and sanitization constraints before extraction. Continue accepting older schema-v6 manifests that predate the editorial ID fields and migrate their databases forward with empty board state.

Import only into a destination that does not exist. Extract declared members to a same-volume temporary directory, open the recovered project with the canonical migration code, verify revision and completed-run integrity plus manifest identities, and atomically rename the directory into place. Never overwrite an existing project.

## Consequences

- A clean installation can reproduce revision IDs, findings, decisions, approvals, applications, and resumable run state from one checked artifact.
- In-progress runs remain recoverable without being misreported as complete.
- Bundle inspection does not establish authorship, malware freedom, or editorial quality; there is no signature or encryption in ER-015.
- Manuscript snapshots remain private, exact originals. The bundle is sanitized operational state, not anonymized prose and not a public-sharing format.
- A manuscript that itself contains a detected credential-like string or private absolute path cannot be exported until the author removes it or uses another protected backup method.
