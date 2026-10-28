# Durable manuscript state and ingestion

This document describes the ER-010/ER-011 implementation in `src/editroast_core/manuscript_state.py`. It is local, standard-library-only code. Manuscript text is always untrusted data and never authorizes command execution, uploads, network access, or other external actions.

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

`schema_migrations` records each applied migration. Schema version 1 creates the durable tables; version 2 imports compatible preview-ledger rows without deleting or renaming the old tables.

| Table | Responsibility |
|---|---|
| `projects` | Project identity, title, author, language, metadata and timestamps |
| `manuscript_revisions` | Parent-linked immutable snapshots, SHA-256, size, format, extraction counts and warnings |
| `chapters` | Revision-local chapter order and stable chapter IDs |
| `paragraphs` | Complete extracted paragraph map, stable IDs, occurrence identity, context, style/runs and source references |
| `paragraph_changes` | Added, removed, changed, unchanged or ambiguous relationships to the previous revision |
| `editorial_findings` | Findings bound to their exact source revision and optional paragraph ID |
| `author_decisions` | Current accepted/approved, rejected, pending or deferred decision state |
| `decision_events` | Append-only decision state history |
| `runs` | Reserved typed run/provenance records for later workflow PRs |
| `audit_log` | Schema, project and revision lifecycle events |

When a project contains the PR-01 preview tables (`revisions`, `findings`, `decisions`), opening it creates corresponding durable revision, finding, decision and event records. The old tables remain available, so `scripts/editroast_state.py --project ...` continues to work. A migrated snapshot that has no old structured paragraph map is labelled `legacy`; its original hash and decision/finding history remain inspectable.

## Revision lifecycle

1. Validate the suffix and fully parse the input before changing project state.
2. For DOCX, validate every ZIP member, decompression limits, encryption/symlinks, and XML safety.
3. Compute the original SHA-256 and structured content-map hash.
4. Reconcile chapters and paragraphs conservatively against the parent revision.
5. Copy and verify the immutable snapshot using an atomic filesystem move.
6. Insert the revision graph, complete structure, change records and audit event in one SQLite transaction.
7. Use `manuscript verify` to detect missing, resized or modified snapshots and paragraph/chapter count mismatches.

Import never rewrites the supplied file. ER-012/ER-013 semantic diff and author-approved change application are not part of this implementation.

## Paragraph identity

A paragraph's permanent `stable_id` is an opaque stored UUID, not a line number or text hash. Matching uses a combination of:

- stable chapter/container identity;
- normalized text fingerprint;
- occurrence number for repeated text;
- preceding and following paragraph fingerprints;
- structural order and reciprocal text similarity for changed paragraphs.

Repeated identical paragraphs always have distinct IDs. Their IDs carry forward by occurrence only when the repeat count remains stable. If insertion/removal makes the mapping underdetermined, the new paragraph gets a fresh ID and `reconciliation_status=ambiguous`. This is intentionally safer than silently binding a future finding or edit to the wrong passage.

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
python scripts/editroast.py manuscript verify ./my-book
```

The `show` and `revisions` output includes source hash, parent, counts, warnings and change totals. `verify` exits with status 2 if integrity checks fail. All commands work without an API key or network service.
