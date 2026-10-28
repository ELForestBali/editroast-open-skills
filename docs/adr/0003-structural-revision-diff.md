# ADR-0003: Conservative structural revision diff and reconciliation

- Status: accepted for PR-03
- Scope: ER-012
- Date: 2026-10-28

## Context

PR-02 provides immutable revisions, durable paragraph IDs, and conservative one-to-one matching. Later approval and re-analysis need an explicit representation for moves, chapter changes, splits, merges, and repeated passages. Automatically forcing a single mapping in underdetermined cases could attach a finding or future edit to the wrong text.

## Decision

Store the parent-to-child comparison as typed diff groups with ordered before/after members. Preserve stable IDs for strong identity evidence, including the primary continuity member of a split or merge. Use exact fingerprints, chapter identity, neighboring context, reciprocal similarity, relative position, and sequence order as structural evidence.

Represent uncertain mappings as unresolved groups with ranked candidates and confidence. Do not mutate revisions or stable IDs when a person resolves them. Instead, append resolve/reopen events and compute an effective relation from the latest event. Keep the original automatic result and every manual decision inspectable.

Require every active before-side and after-side stable ID to have at most one owning group. Permit one-to-many split and many-to-one merge membership inside that owner, but reject incompatible cross-group reuse. A reopen event releases the group's active claims.

Expose lexical changes in names/capitalized terms, numbers, and negation tokens as risk signals only. Do not label them as proven changes of meaning or authorial intent.

Retain `paragraph_changes` as the PR-02 compatibility view while storing the richer representation in schema version 3. Backfill existing projects without deleting legacy state. Omit manuscript text from CLI diff output by default.

## Consequences

- ER-013 can consume explicit groups, stable IDs, resolution status, and history without re-diffing the manuscript.
- Correction lists and editorial re-analysis can target changed structural units.
- Ambiguity remains visible and blocks implicit acceptance.
- Manual reconciliation records identity decisions but does not approve or apply prose changes.
- The implementation does not edit DOCX, render reports, or claim semantic understanding.
