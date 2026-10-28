# Historical feature provenance

This mapping records what was inspected for ER-001 and what PR-01 deliberately carries forward. The two handoff archives are development inputs, not distributable replacements for this repository. No `legacy/` tree, private manuscript, customer report, `.env`, credential, or author correspondence is imported.

## Sources inspected

| Source | Role | Public import policy |
|---|---|---|
| Public `editroast-open-skills` v1 at `9a1679f` | Working roast/proofreading baseline | Preserve behavior and CLI paths |
| `EditRoast-Skills-source-baseline.zip` | Sanitized historical reference from `Skills.7z` | Selectively reproduce reviewed behavior; never bulk-copy |
| `EditRoast-v2-integration-preview.zip` | Pre-release four-skill and local-ledger prototype | Reconcile architecture; do not preserve copied Core files |
| Product Brief v0.7, Product Specification v1.0, Backlog v1.0, `START-HERE.md` | Product requirements and boundaries | Requirements only; not shipped as skill instructions |

## Feature mapping

| Historical feature | Historical source | New owner | PR-01 status |
|---|---|---|---|
| Three editor personas plus Editorial Board | `prozharka-korrektura/CLAUDE.md`, `README.md`, and prompt modules | `manuscript-roast` | Stable IDs and exact reviewed RU/EN/ES names preserved in `references/editorial-personas.md`; full v2 review orchestration remains a later editorial PR |
| Branded editorial reports and editroast.com QR | `prozharka-korrektura` branding/render scripts and assets | `manuscript-roast`, `manuscript-proofreading`; future translation decisions report | Provenance preserved, but renderer and binary branding assets are not published in PR-01 because dependency, layout, and asset-license review are incomplete |
| Translation ambiguity sheet, author review, glossary, paragraph integrity | `perevod/SKILL.md`, `extract_to_md.py`, `check_paragraph_integrity.py` | `literary-translation` (BETA) | Decision/glossary contract and offline compatibility ledger packaged; branded PDF, pilot translation, segment invalidation, and full translation are unsupported |
| KDP formatting and pagination lessons | `verstka/README.md`, `style_check.txt`, `kdp_cover_lib.py` | `book-production` (BETA) | Safe lessons documented as non-universal references; no conversion, preflight engine, cover build, EPUB, or KDP acceptance claim |
| Synopsis workflow | `sinopsis/README.md` placeholder | Future, unassigned | Unsupported; no `synopsis` skill is created |
| Existing local chunking, resume, evidence validation | Public v1 `scripts/manuscript_workflow.py` | `editroast_core`, roast, proofreading | Preserved under the historical CLI path and deterministically bundled |
| Preview revision/decision ledger | Four copied preview `editroast_state.py` files | `src/editroast_core/editroast_state.py` | One canonical compatibility implementation; explicitly not the final ER-010 schema |

## Exact persona localization

The historical reviewed display names are:

- `reader_market`: Лёня Тираж / Leo Shelf / Leo Tirada
- `voice_emotion`: Вера Зоркая / Vera Sharp / Vera Aguda
- `structure_logic`: Борис Точка / Boris Marker / Boris Punto
- `editorial_board`: Редсовет / СанСаныч / Editorial Board / Chief Sterling (chair) / Consejo Editorial

The stable order is reader → voice → structure → board. Other locales must retain a reviewed name or a clearly neutral fallback; an agent must not improvise an alias and present it as historical localization.

## Deliberately unsupported after PR-01

- live LLM/provider calls, cloud consent implementation, and spend controls;
- real manuscript rewriting or approved-change application;
- branded PDF/DOCX rendering and QR verification;
- end-to-end literary translation or automated source-change invalidation;
- DOCX/EPUB/print-PDF production, KDP profile validation, or upload;
- synopsis/query workflow;
- publication, outreach, email delivery, or website deployment.

Those omissions are scope boundaries, not silent feature completion.
