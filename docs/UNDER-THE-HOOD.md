# Under the hood: why EditRoast is more than a prompt pack

> **Implementation snapshot:** EditRoast Open Skills `2.0.0-alpha.7` development branch (28 October 2026). This page describes implemented, inspectable functionality; future features are explicitly labelled as such. Release/PR notes record the final commit SHA.

EditRoast does use instructions and an AI model for judgments about literary quality. That is intentional. The distinction from a plain prompt template is the **code that prepares a whole manuscript, retains persistent state, verifies evidence, packages each skill independently, and makes the workflow auditable**. This does not mean the software can prove that an editorial opinion is correct.

## 1. Architecture

```text
Author's DOCX / TXT / Markdown (unchanged source)
         │
         ▼
Installed SKILL.md                 ← explains workflow to the selected AI agent
         │
         ├── Prepare via local Python CLI
         │     ├── input/ original copy
         │     ├── work/manifest.json (chapters and stable chunk IDs)
         │     ├── work/chunks/ (line-addressable text)
         │     ├── work/state.json (completed / pending chunks)
         │     ├── work/book-map.md
         │     └── work/evidence.jsonl
         │
         ├── Agent reads whole-book map and reviews individual chunks
         │     ├── ER-020 Pass A / Pass B use ER-014 run checkpoints
         │     └── canonical schema-v6 map + verified unit results
         │
         ├── Local evidence and coverage validation
         │     └── verified chapter/chunk references, source-matching quotes
         │
         └── output/ Markdown editorial artifacts
```

The maintained runtime lives in [`src/editroast_core/`](../src/editroast_core/). `scripts/manuscript_workflow.py` and `scripts/editroast_state.py` are thin public-CLI compatibility wrappers. Each skill's `SKILL.md` describes the relevant agent workflow. The build step vendors the shared runtime into standalone release packages; there are **not four independently hand-maintained forks of Core**.

For the precise architectural decision, see [ADR-0001](adr/0001-canonical-core-and-skill-bundles.md) and the [historical feature mapping](PROVENANCE.md).

## 2. What the Python code does

**Manuscript preparation:** Extracts readable DOCX paragraphs with Python's XML and ZIP standard libraries, reads plain text and Markdown, detects common chapter heading styles, counts words, and splits long chapters into bounded chunks. It creates stable chapter/chunk IDs and numbered source lines. It does not preserve tracked changes or promise perfect reconstruction of complex Word layouts.

**Checkpoint and resumption:** A manifest and state file mark work as pending or complete. A new session can inspect the workspace and continue from pending chunks. Successful resumption still depends on the agent following the skill instructions and saving its report files.

**Evidence validation:** The offline validator checks chunk/report coverage, valid source line ranges, and whether quoted passages actually match extracted source text after whitespace normalization. This catches missing chapters and fabricated or miscopied *quotes*. It cannot fact-check literary claims, assess thematic insight, or guarantee that the AI noticed every problem.

**Whole-book map:** ER-020 uses two chapter-scoped ER-014 runs. Pass A
inventories global structure and provisional actors/context; Pass B receives
compact verified context and performs evidence-linked chapter synthesis. Core
validates record classes, narrative frames, exact quotes/offsets, record links,
full Pass-A review coverage and source hashes before writing the canonical map
to SQLite. Integrity validation and editorial-quality scorecards remain
separate.

**Preliminary decision ledger:** A small SQLite CLI snapshots imported versions with SHA-256, records findings/statuses, collects candidate translation decisions, exports CSV, and blocks progression while there are pending translation decisions. This is compatibility scaffolding from a preview. It is **not yet** the future revision engine: it does not automatically apply approved changes, track paragraphs across rewritten versions, or translate books.

**Distribution:** The Python release builder composes independently installable archives from a single source tree, sorts inputs, normalizes ZIP metadata, and writes SHA-256 checksums. The installer targets isolated Codex and Claude Code project folders. Directly copying raw `skills/<name>/` source folders is insufficient because those folders do not yet contain the vendored runtime.

## 3. What is implemented in each skill?

| Skill | Implemented in this snapshot | Not implemented yet |
|---|---|---|
| `manuscript-roast` | Multi-pass full-book review instructions; ER-020 two-pass canonical book map; stable chunks; resumable state; evidence and coverage checks; Markdown outputs; reviewed virtual editor names | ER-021 multi-persona orchestration and one comparative re-review report; polished branded PDF with QR |
| `manuscript-proofreading` | Full-book proofreading instructions, style/consistency tracking, line-grounded findings and reporting via the local workflow | Apply-to-DOCX with verified tracked changes, permanent cross-revision finding matching |
| `literary-translation` **BETA** | Rules for author approval, ambiguous-term inventory and glossary/decision ledger | End-to-end book translation, author-interview PDF, ongoing alignment/invalidation, literary QA |
| `book-production` **BETA** | Destination/intake and safety guidance, KDP-specific lessons in reference materials | Generating print-ready PDF, EPUB, DOCX, KDP preflight/acceptance, cover construction |

Leo Shelf, Vera Sharp, Boris Marker, and Chief Sterling as chair of the Editorial Board represent different **editorial lenses**, not four paid human editors or four independently deployed models.

## 4. Source size and reproducibility (fixed commit snapshot)

Counted on commit `4f4ba3eb70ba0e8ca21666df935fda51ce18969f`, with physical lines **including blank lines**, excluding generated archives, examples, vendored copies and Markdown/YAML prompts:

| Maintained Python category | Files | Physical lines |
|---|---:|---:|
| Canonical `src/editroast_core/` | 3 | **706** |
| `install.py` and `scripts/` (CLI/build/security tools) | 6 | **525** |
| `tests/` Python test modules | 3 | **315** |
| **Total** | **12** | **1,546** |

These are **not** 1,546 lines of novel AI algorithms. The number includes compatibility wrappers and test/support code, and says nothing on its own about editorial quality. There are ten `unittest` test methods. The PR-01 GitHub Actions run reported 12 successful matrix checks, which are CI jobs rather than 12 distinct unit tests.

Reproduce key checks on the PR-01 source checkout:

```bash
python scripts/build_release.py
python scripts/security_check.py
python -m unittest discover -s tests -v
```

The builder produces four individual skill ZIPs, one complete-pack ZIP, and a `SHA256SUMS.txt` manifest. Packaging tests rebuild artifacts in clean temporary directories, compare hashes, check bundle completeness, and smoke-test project installation targets. The security script checks known disallowed paths, secrets and archive traversal patterns. These checks **are not a third-party security audit** or a substitute for evaluating the user-selected AI agent.

## 5. Boundaries: local code, cloud inference, and BYOK

The bundled Python scripts do not make live AI API requests, send email, or transmit manuscripts to EditRoast. They can prepare, chunk and validate files offline without an API key. The **selected AI agent** must still actually analyze the text. If the user uses cloud AI, manuscript excerpts go to that provider under its own terms; “local preparation” is not “offline AI inference.”

There is no hosted EditRoast model and no mandatory EditRoast account. The skills can use an agent's existing provider configuration. A ChatGPT/Claude browser subscription is **not** an API key or a guarantee of automatic filesystem execution. The ordinary-chat workflow is a manual alternative, not feature-equivalent to a local coding agent.

## 6. What remains on the roadmap

The [project issues](https://github.com/ELForestBali/editroast-open-skills/issues) and [CHANGELOG](../CHANGELOG.md) track planned work. The staged roadmap includes safe author-approved text revisions and version comparisons; professionally designed branded editorial PDFs and QR links; genuinely end-to-end literary translation with author-approved cultural decisions; publishing profiles and output validation for KDP/EPUB/DOCX; and a tested BYOK provider interface. **Do not claim those capabilities as shipped until corresponding acceptance tests pass.**

## 7. How to examine the product critically

1. Read a [skill definition](../skills/manuscript-roast/SKILL.md) and the [canonical manuscript workflow source](../src/editroast_core/manuscript_workflow.py).
2. Follow the `prepare → status → complete → validate` CLI on [synthetic test fixtures](../tests/fixtures/); do not upload an unpublished book to a public issue.
3. Inspect [tests](../tests/), [privacy and license assumptions](PRIVACY-SECURITY-LICENSES.md), and [ADR-0001](adr/0001-canonical-core-and-skill-bundles.md).
4. Run the builder and tests yourself. Report a reproducible defect with a synthetic excerpt and command output.
5. For editorial-quality evaluation, independently review correctness, voice preservation and the usefulness of recommendations; automated evidence checks do **not** measure those dimensions.

### Frequently asked questions

**Is it a prompt wrapper?** It includes prompts and agent instructions, but also maintained executable code with deterministic data processing, persisted workspaces, source evidence checks, version/decision scaffolding, standalone packaging and tests. The two parts are complementary.

**Can it edit any book in one click?** Not yet. The orchestration is guided by compatible agents, and full-text rewriting requires additional, tested features.

**Can it guarantee all language pairs or publisher acceptance?** No. Language quality is model-dependent; translation and book production are BETA foundations.

**Does open-source mean free AI inference?** No. EditRoast's code is MIT licensed and free to download; selected AI providers may charge separately.

This documentation is meant for authors, reviewers, developers, technical writers, researchers and agent-based discovery tools. Use the source as the authority for implementation claims and the published roadmap only for future intentions.
