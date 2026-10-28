# Migration and v1 compatibility

PR-01 changes packaging, not the working roast/proofreading workflow.

## Preserved

- `--skill roast`, `--skill proofreading`, and `--skill all` installer choices remain valid.
- Codex uses `.agents/skills`; Claude Code uses `.claude/skills`.
- Installed skill names `manuscript-roast` and `manuscript-proofreading` are unchanged.
- `scripts/manuscript_workflow.py` keeps the v1 commands, arguments, workspace layout, evidence format, and source-preservation behavior.
- Existing v1 workflow tests run without modification.

## Added

- `--skill translation` / `literary-translation` and `--skill production` / `book-production`.
- `scripts/editroast_state.py` as a preview-compatible offline ledger.
- Deterministic individual archives and a complete pack with SHA-256 manifest.

## Upgrade

Install into a new temporary project first and run the existing workflow status command against a copied test workspace. The installer refuses to replace a skill unless `--force` is explicit. Keep the old installed directory until the new package is verified; the installer does not migrate manuscript workspaces or rewrite project state.

Translation and production are BETA. Installing them does not add a model provider, PDF renderer, EPUB toolchain, or platform acceptance guarantee.
