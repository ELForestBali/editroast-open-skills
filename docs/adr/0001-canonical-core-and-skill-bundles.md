# ADR-0001: Canonical Core and generated standalone skill bundles

- Status: accepted for PR-01
- Scope: ER-001 through ER-004
- Date: 2026-10-28

## Context

Public v1 installed shared scripts into each selected skill. The integration preview then stored four manually copied `editroast_state.py` files. That layout makes fixes drift and leaves direct skill packages dependent on repository-relative files.

## Decision

`src/editroast_core/` is the only maintained runtime source. It contains the existing offline manuscript workflow and the preview-compatible local ledger. Repository-level files in `scripts/` are stable CLI entry points, not second implementations.

`scripts/build_release.py` stages each skill, vendors the two CLI entry points, the complete `editroast_core` package, and shared templates, then writes a deterministic ZIP. Generated duplication exists only in staged/installable artifacts. Builds use sorted paths, fixed ZIP metadata, and SHA-256 manifests.

Source installs run the same staging function before copying. Built-artifact installs copy an already self-contained skill. Both Codex and Claude Code destinations are exercised in temporary directories.

## Compatibility interfaces

The following public v1 entry point remains valid:

```text
python <installed-skill>/scripts/manuscript_workflow.py <command> ...
```

Its commands and workspace contract remain `prepare`, `status`, `complete`, and `validate`. The implementation now loads `editroast_core.manuscript_workflow`.

The preview ledger remains available at:

```text
python <installed-skill>/scripts/editroast_state.py --project <path> <command> ...
```

Its tables and command output are compatibility scaffolding only. ER-010 owns the durable v2 schema, migrations, consent records, and stable anchors. PR-01 must not freeze this preview schema as the product contract.

## Consequences

- Runtime fixes happen once in `src/editroast_core/`.
- Every built skill is independently installable without repository-relative imports.
- Direct installation from an unbuilt `skills/<name>` folder is incomplete; use `install.py` or a built archive. This limitation is documented until a published installer can build or fetch a verified artifact.
- Translation and production packages can share safe state/utility code while remaining clearly BETA.
