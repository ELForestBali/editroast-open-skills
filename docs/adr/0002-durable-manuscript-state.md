# ADR-0002: Durable manuscript state and conservative anchors

- Status: accepted for PR-02
- Scope: ER-010 and ER-011
- Date: 2026-10-28

## Context

The PR-01 compatibility ledger stores revision files, findings and translation choices, but has no project metadata, structured manuscript graph, schema history, integrity inspection or reliable paragraph identity. Its table names are already a public preview interface, so replacing them in place would risk losing user state and breaking the legacy CLI.

DOCX is also a ZIP/XML container supplied by the user. Treating it as a trusted archive risks traversal or decompression abuse, while flattening it to nonempty strings loses empty paragraphs, formatting runs and future round-trip references.

## Decision

Keep `src/editroast_core/` as the only maintained runtime. Add a versioned durable schema beside the old preview tables and migrate preview rows forward without deleting them. Store each imported source as immutable original bytes in a parent-linked revision directory, with SHA-256, size, extraction counts and a structured content-map hash.

Represent paragraphs with opaque stable IDs. Use chapter/container identity, normalized fingerprints, repeated occurrence identity, neighbor context and conservative structural similarity to relate revisions. Never use a line number or text hash alone as permanent identity. Mark underdetermined matches `ambiguous` and require a later reconciliation workflow.

Validate DOCX members and decompression bounds before parsing. Preserve every body paragraph, including empty paragraphs, mixed run metadata and source references. Report meaningful structures that are preserved only in the original snapshot.

Use atomic file moves and SQLite transactions. On reopen, remove only incomplete staging files and snapshot directories that have no database record. Provide an offline CLI for creation, import, inspection and integrity verification.

## Consequences

- Old preview-ledger commands and tables remain usable.
- New revisions, decisions and findings are durable and inspectable without a provider or API key.
- Original source bytes remain recoverable and corruption is detectable.
- Ambiguity is visible instead of becoming an unsafe automatic link.
- This PR does not apply edits, provide semantic diff, or promise lossless DOCX reconstruction; those remain ER-012/ER-013 work.
