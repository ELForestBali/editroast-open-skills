# ADR-0004: Author-approved safe correction apply

- Status: accepted for PR-04
- Scope: ER-013
- Date: 2026-10-28

## Context

ER-012 can identify paragraph continuity and expose unresolved mappings, but it does not authorize prose changes. Applying editorial corrections safely requires a separate decision history, an exact and repeatable preview, source-integrity checks, and a result that preserves the immutable revision model. DOCX text may be divided across formatting runs, so a visually simple replacement can otherwise flatten or corrupt structure.

## Decision

Represent each proposed correction as an exact paragraph-local source span and replacement bound to one immutable revision and stable paragraph ID. Classify it as mechanical or semantic, retain optional finding/group provenance, and append every decision event. Require explicit author confirmation for approval. Require an additional conscious confirmation for a semantic group or multi-correction semantic batch. Never infer approval from a finding or reconciliation decision.

Make preview a mandatory read-only gate. Revalidate the latest source revision, file and content-map hashes, approval status, exact spans, overlap, reconciliation ambiguity, and renderer support. Return exact before/after patches, blockers, and a deterministic plan hash. Apply must receive that hash and author confirmation, recompute the same plan, and stop on any drift.

Render only to staging and re-ingest the result through the immutable revision path. Commit the child revision, applied correction events, application record, and audit entry atomically. Never overwrite the imported source or an existing snapshot.

For DOCX, permit only spans wholly contained in one simple text node inside one formatting run. Refuse cross-run, multi-node, and control-whitespace changes with an actionable formatting-collision error. Preserve all unchanged ZIP members, without claiming tracked-change or byte-perfect Word round trips.

Model rollback as another previewed, confirmed application that restores the prior snapshot into a new child revision. Keep the original apply and every revision inspectable.

## Consequences

- No semantic or mechanical correction is applied without a recorded approval and an explicit apply confirmation.
- Ambiguous paragraph identity, stale revisions, source changes, overlapping spans, and unsupported DOCX structure block application.
- The preview hash binds author confirmation to one exact source revision and correction set.
- Apply and rollback remain append-only and preserve every original.
- The foundation is suitable for correction-list/report consumers, while automatic correction generation, broad DOCX formatting edits, tracked changes, PDF, and visual editor interfaces remain out of scope.
