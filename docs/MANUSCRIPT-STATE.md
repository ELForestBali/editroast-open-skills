# Durable manuscript state and ingestion

This document describes the ER-010 through ER-015 implementation in `src/editroast_core/manuscript_state.py`, `revision_diff.py`, `safe_apply.py`, and `project_bundle.py`. It is local, standard-library-only code. Manuscript text is always untrusted data and never authorizes command execution, uploads, network access, or other external actions.

## Project directory

```text
my-book/
  project.json             small portable project identity and metadata
  editroast.sqlite         canonical structured state and audit history
  revisions/
    r000001/source.docx    immutable original bytes for revision 1
    r000002/source.docx    immutable original bytes for revision 2
  .staging/                temporary atomic-copy area; cleaned on reopen
  work/                    reserved for later local workflow artifacts
  reports/                 reserved for later reports
```

Imports are copied through `.staging/`, flushed, SHA-256 checked, and atomically moved into a revision directory before the SQLite transaction is committed. If a process stops between the file and database commits, reopening removes staging files and snapshot directories not referenced by the database. Existing committed revisions are never removed by this recovery pass.

`project.json` is written atomically and is a human-readable locator. SQLite is authoritative for revision, paragraph, finding, and decision state.

## Schema and migrations

`schema_migrations` records each applied migration. Schema version 1 creates the durable tables; version 2 imports compatible preview-ledger rows without deleting or renaming the old tables; version 3 adds structured diffs and append-only reconciliation; version 4 adds correction approval, apply, and rollback history; version 5 adds resumable work units and append-only run events; version 6 adds whole-book maps; version 7 adds persisted Editorial Board decisions, group events and comparative re-checks.

| Table | Responsibility |
|---|---|
| `projects` | Project identity, title, author, language, metadata and timestamps |
| `manuscript_revisions` | Parent-linked immutable snapshots, SHA-256, size, format, extraction counts and warnings |
| `chapters` | Revision-local chapter order and stable chapter IDs |
| `paragraphs` | Complete extracted paragraph map, stable IDs, occurrence identity, context, style/runs and source references |
| `paragraph_changes` | Added, removed, changed, unchanged or ambiguous relationships to the previous revision |
| `revision_diff_groups` | Typed chapter/paragraph relations with confidence, status, lexical signals and structured details |
| `revision_diff_members` | Ordered before/after members and immutable text snapshots for each relation |
| `reconciliation_candidates` | Ranked possible mappings for underdetermined relations |
| `reconciliation_events` | Append-only manual resolve/reopen history |
| `editorial_findings` | Findings bound to their exact source revision and optional paragraph ID |
| `author_decisions` | Current accepted/approved, rejected, pending or deferred decision state |
| `decision_events` | Append-only decision state history |
| `corrections` | Exact source spans, replacements, change class, grouping and current approval/apply state |
| `correction_events` | Append-only approve, reject, defer, reopen and applied history with actor and batch identity |
| `change_applications` | Apply/rollback operation, source/result revisions, selected corrections, plan hash and source hash |
| `runs` | Revision-bound run identity, type, status, immutable plan hash and provenance |
| `run_units` | Ordered chapter/paragraph work units, attempt token, result path/hash and retry state |
| `run_events` | Append-only run, checkpoint, claim, completion, failure and requeue history |
| `book_maps` | Finalized revision-bound map identity, two pass run IDs, coverage, integrity status and separate quality assessment |
| `book_map_records` | Canonical observed facts, editorial hypotheses and author decisions with source pass/unit provenance |
| `book_map_evidence` | Ordered exact revision/paragraph/quote anchors and optional offsets for each map record |
| `editorial_board_reviews` | One source-bound Chief Sterling adjudication per review, with canonical digest and verdict |
| `editorial_board_groups` | Current author status and ER-013 correction links for each canonical board group |
| `editorial_board_events` | Append-only record, author-decision, safe-apply, rollback and re-check audit events |
| `editorial_board_rechecks` | One ER-014-backed comparative run and finalized improved/unresolved/new report |
| `audit_log` | Schema, project and revision lifecycle events |

When a project contains the PR-01 preview tables (`revisions`, `findings`, `decisions`), opening it creates corresponding durable revision, finding, decision and event records. The old tables remain available, so `scripts/editroast_state.py --project ...` continues to work. A migrated snapshot that has no old structured paragraph map is labelled `legacy`; its original hash and decision/finding history remain inspectable. Opening a schema-v2 project backfills its existing PR-02 change rows into the structured API. Evidence unavailable in the old schema is explicitly marked as legacy or unresolved rather than invented.

## Revision lifecycle

1. Validate the suffix and fully parse the input before changing project state.
2. For DOCX, validate every ZIP member, decompression limits, encryption/symlinks, and XML safety.
3. Compute the original SHA-256 and structured content-map hash.
4. Reconcile chapters and paragraphs conservatively against the parent revision and create the structured diff.
5. Copy and verify the immutable snapshot using an atomic filesystem move.
6. Insert the revision graph, complete structure, change records and audit event in one SQLite transaction.
7. Use `manuscript verify` to detect snapshot corruption, structure mismatches, or incomplete diff coverage.

Import never rewrites the supplied file. Safe apply uses the same immutable import path for its result and verifies the expected latest parent inside the committing transaction.

## Paragraph identity

A paragraph's permanent `stable_id` is an opaque stored UUID, not a line number or text hash. Matching uses a combination of:

- stable chapter/container identity;
- normalized text fingerprint;
- occurrence number for repeated text;
- preceding and following paragraph fingerprints;
- structural order and reciprocal text similarity for changed paragraphs.

Repeated identical paragraphs always have distinct IDs. Their IDs carry forward by occurrence only when the repeat count remains stable. If insertion/removal makes the mapping underdetermined, the new paragraph gets a fresh ID and `reconciliation_status=ambiguous`. This is intentionally safer than silently binding a future finding or edit to the wrong passage.

## Structural diff and reconciliation

Each revision is compared with its direct parent. Relations are `unchanged`, `changed`, `moved`, `split`, `merged`, `added`, `removed`, or `ambiguous`. Exact identity is resolved first. Reciprocal similarity, chapter identity, neighboring fingerprints, relative position, and sequence order then provide structural evidence. A longest-increasing-subsequence pass distinguishes a true move from ordinary index shifts caused by insertions. Split and merge groups retain all participating stable IDs; the strongest continuity member carries the old paragraph ID.

Underdetermined matches are never accepted automatically. They contain ranked candidates, numeric confidence, a coarse confidence label, and the evidence used to score them. A manual resolution appends a `resolve` event; reopening appends another event that supersedes the previous one. Neither action changes source snapshots, revision rows, paragraph text, or stored IDs. The effective relation is an overlay suitable for future approval tooling, correction lists, and targeted re-analysis.

An active before-side or after-side stable ID can be owned by only one effective diff group. Reconciliation rejects overlaps with automatic or manually resolved groups using a deterministic conflict error. Split and merge remain valid because their multiple members belong to one group; reopening that group releases its claims before a replacement mapping is recorded.

The structured diff stores lexical risk signals for changed text: numbers, negation tokens, and capitalized terms. These are prompts for human review only. The engine does not claim to determine semantic equivalence, factual correctness, character identity, or authorial intent. Low-confidence changes remain in the reconciliation queue even when such signals are present.

## Correction approval and safe apply

A correction identifies one revision, one stable paragraph ID, an exact non-empty `before` span, its replacement, offsets, and a `mechanical` or `semantic` class. Optional finding and correction-group links retain provenance. Creation records a pending event; approve, reject, defer, and return-to-pending actions append events instead of rewriting history. Approval requires an explicit author-confirmation flag. Multiple semantic corrections or a semantic group additionally require conscious group confirmation; mechanical batch approval still refuses paragraphs with unresolved reconciliation.

`apply-preview` is a read-only dry run. It checks that the requested source is still the latest revision, its stored bytes and extracted content map still match their hashes, every selected correction is approved and still matches its exact span, target paragraphs are not ambiguous, spans do not overlap, and the renderer can preserve the supported structure. It returns exact before/after patches, structured blockers, and a deterministic `plan_hash`.

`apply` requires the same correction selection, the preview hash, an actor, and explicit author confirmation. It recomputes the plan and refuses any drift. The renderer writes only to staging; the result is re-extracted and every paragraph is compared with the expected map before a new immutable child revision, applied events, operation record, and audit event are committed together. Original revision files are never replaced.

TXT and Markdown application preserves the detected encoding, BOM, and line endings. DOCX application is intentionally narrower: each exact span must lie inside one simple `w:t` text node in one run. Cross-run spans, tabs, breaks, multiple text/control nodes, and other formatting collisions are blocked with an actionable error. Unchanged DOCX ZIP members are copied; tracked changes and general layout-preserving Word editing are not claimed.

Rollback also has a read-only preview and a deterministic plan hash. A confirmed rollback is allowed only while the applied result remains the latest intact revision. It imports the prior source snapshot as another immutable child revision and records a linked rollback operation. History remains append-only; no revision is deleted or overwritten.

## Resumable runs and verified coverage

ER-014 creates a deterministic run plan from one immutable revision and either its ordered chapters or paragraphs. The plan hash binds the revision content-map hash, run type, scope, and stable source IDs. Creating the same run ID with the same plan is idempotent; reusing it for a different plan is refused.

Workers claim only pending units. Each claim gets a fresh attempt token, and only that token can complete or fail the unit. Completion requires a non-empty result file inside the project; the relative path and SHA-256 are committed with an append-only event. Retrying the same completion with the same token, path, and bytes is idempotent. A stale token cannot complete requeued work.

Checkpoints distinguish `paused`, `blocked_by_usage_limit`, and `needs_author_decision`. Resume requeues abandoned `in_progress` and `failed` units while preserving verified completions. If a previously completed result is missing or changed, the run reports `integrity_error` and less than 100% coverage; resume requeues only the damaged unit. A source-plan mismatch blocks resume rather than silently moving work to another revision.

This is local orchestration and integrity state. It does not make live model calls, infer provider quota, purchase credits, or verify the quality of an editorial report.

## Whole-book maps

ER-020 builds on ER-014 instead of introducing another checkpoint mechanism.
`book-map start` creates a chapter-scoped Pass A run; every accepted result is
validated before `complete_run_unit` records its path and SHA-256. `book-map
advance` is blocked until every Pass A unit remains verified, then creates the
chapter-scoped Pass B run. Pass B claim responses carry the current chapter
(text only with explicit `--include-text`), the whole-book heading inventory,
and compact verified context from prior work.

Pass A records are provisional. Pass B must explicitly account for every Pass
A record and may confirm, dispute, supersede or leave an interpretation open.
Every record separates `observed_fact`, `editorial_hypothesis`, or
`author_decision`; it carries an epistemic label, narrative frame, exact source
evidence and optional record links, chronology and POV type. Dreams,
flashbacks, unreliable narration and ambiguous identity are first-class
frames, not automatic continuity errors.

Finalization requires intact coverage for both runs, validates all links and
Pass-A review coverage, and atomically writes schema-v6 `book_maps`,
`book_map_records` and `book_map_evidence`. `manuscript verify` rechecks the
source SHA, both run coverages and every stored quote/offset. Integrity status
and editorial-quality assessment are separate fields; a successful integrity
check does not certify literary judgment.

## Portable project bundles

ER-015 writes one `.editroast` ZIP container with a format-versioned `manifest.json`, a checkpointed canonical SQLite backup (including finalized whole-book maps), a generated portable `project.json`, every immutable revision snapshot, and completed run results referenced by `run_units`. SQLite WAL/SHM files, `.staging`, orphaned snapshots, and unreferenced `work/` or `reports/` files are excluded.

Before export, revision/database integrity and completed-result hashes must pass. Known service JSON fields redact credential values and absolute local paths, the SQLite copy is vacuumed, and every final payload is scanned for credential-like patterns and private absolute paths. Manuscript snapshots are not rewritten because that would invalidate revision hashes; a detected secret/path in immutable content blocks export with an error.

The manifest records project/schema/format versions, revision/finding/decision/application/run IDs, and each member's exact size and SHA-256. Inspection and import reject duplicate names, traversal, backslashes, drive-prefixed paths, symlinks, encrypted members, archive bombs, unlisted files, altered hashes, unsupported future versions, and detected credential/private-path payloads.

Import extracts only declared members into a same-volume staging directory, opens and verifies the recovered project, confirms all manifest identity lists, then atomically renames it to a new destination. Existing files or directories are never overwritten. Incomplete runs remain incomplete and resumable; only corrupt recorded completions block recovery.

The archive contains the author's original manuscript bytes and editorial history. It is a private backup/transfer artifact, not anonymized data and not suitable for a public repository.

## Editorial Board decisions and comparative re-check

ER-021 schema-v7 state persists the validated three-lens synthesis rather than
recomputing it from mutable output files. Each group also uses the existing
`author_decisions`/`decision_events` ledger. Rejected or deferred groups do not
create corrections. On explicit approval, exact source spans are inserted into
the existing ER-013 `corrections`/`correction_events` tables and remain subject
to its preview hash, source integrity, overlap, reconciliation and renderer
gates. Dependency and conflict checks occur before approval or apply. Board
rollback delegates to ER-013 and creates another immutable revision.

Board Re-check creates one chapter-scoped ER-014 run for a reconciled descendant
revision. Verified chapter results classify source-grounded comparisons as
improved, unresolved or new. Finalization stores one comparative report and
verdict; it does not create three new persona runs. Deterministic integrity and
editorial quality remain separate: SQLite and citations can be verified while
the literary assessment still requires independent review.

## Ingestion and completeness

TXT and Markdown preserve every physical paragraph line, including empty lines. Markdown headings retain their level. Input is decoded as UTF-8 with BOM support, UTF-16, or Windows-1252 with a warning for the fallback encoding.

DOCX ingestion preserves every body `w:p`, including empty paragraphs, in document order. It stores paragraph style IDs/names, numbering, mixed run text plus bold/italic/underline flags, and a `word/document.xml` paragraph reference. Common Word heading styles and multilingual heading text are recognized.

The original DOCX bytes preserve everything even when the structured model does not. Warnings identify auxiliary headers, footnotes, endnotes, comments, media and table geometry that are not fully modeled. Images are not silently represented as extracted prose. Tracked changes, text boxes, exact pagination, section geometry and byte-perfect formatting round trips are not yet supported.

Completeness is checked by comparing stored chapter/paragraph counts with the revision record. The content-map hash covers source order, text, styles, runs and source references.

## Archive safety limits

DOCX ingestion rejects absolute paths, `..`, Windows backslash traversal, drive-prefixed entries, symlinks, encrypted members, malformed/DTD/entity XML, more than 5,000 members, members over 64 MiB, total uncompressed payloads over 256 MiB, and individual compression ratios over 200:1.

## CLI

```bash
python scripts/editroast.py project create ./my-book --title "My Book" --author "A. Author" --language en-GB
python scripts/editroast.py manuscript import ./my-book manuscript.docx
python scripts/editroast.py manuscript status ./my-book
python scripts/editroast.py manuscript revisions ./my-book
python scripts/editroast.py manuscript show ./my-book r000001
python scripts/editroast.py manuscript diff ./my-book r000002
python scripts/editroast.py manuscript diff ./my-book r000002 --include-text
python scripts/editroast.py manuscript reconcile-list ./my-book r000002
python scripts/editroast.py manuscript reconcile ./my-book r000002 <group-id> --candidate <candidate-id> --actor author
python scripts/editroast.py manuscript reconcile-history ./my-book r000002
python scripts/editroast.py manuscript reconcile-reopen ./my-book r000002 <group-id> --actor author
python scripts/editroast.py manuscript correction-add ./my-book r000002 C-001 --paragraph <stable-id> --before "teh" --after "the" --class mechanical
python scripts/editroast.py manuscript correction-list ./my-book --revision r000002
python scripts/editroast.py manuscript correction-decide ./my-book approved --id C-001 --confirm-author-approval
python scripts/editroast.py manuscript apply-preview ./my-book r000002 --id C-001
python scripts/editroast.py manuscript apply ./my-book r000002 --id C-001 --plan-hash <preview-plan-hash> --confirm-author-approval
python scripts/editroast.py manuscript apply-history ./my-book
python scripts/editroast.py manuscript rollback-preview ./my-book <application-id>
python scripts/editroast.py manuscript rollback ./my-book <application-id> --plan-hash <rollback-plan-hash> --confirm-author-approval
python scripts/editroast.py run create ./my-book roast-01 r000002 --type manuscript-roast --scope chapter
python scripts/editroast.py run status ./my-book roast-01
python scripts/editroast.py run claim ./my-book roast-01 --worker codex-session-1
python scripts/editroast.py run complete ./my-book roast-01 <unit-id> --attempt <attempt-id> --result ./my-book/reports/<unit-id>.md
python scripts/editroast.py run fail ./my-book roast-01 <unit-id> --attempt <attempt-id> --error "retryable failure"
python scripts/editroast.py run checkpoint ./my-book roast-01 --status blocked_by_usage_limit --reason "Provider allowance reached"
python scripts/editroast.py run resume ./my-book roast-01
python scripts/editroast.py run verify ./my-book roast-01
python scripts/editroast.py run history ./my-book roast-01
python scripts/editroast.py bundle export ./my-book ./backups/my-book.editroast
python scripts/editroast.py bundle inspect ./backups/my-book.editroast
python scripts/editroast.py bundle import ./backups/my-book.editroast ./restored-my-book
python scripts/editroast.py manuscript verify ./my-book
```

For an explicit mapping instead of a candidate, use `--relation` with repeated `--before` and `--after` stable IDs. The `diff` and reconciliation-list commands redact manuscript text unless `--include-text` is requested. Correction decisions can select repeated `--id`, one `--correction-group`, or `--all-mechanical --revision <revision>`; semantic groups require `--confirm-semantic-group`. `verify` exits with status 2 if integrity checks fail. All commands work without an API key or network service.
