# EditRoast Open Skills

Free, open-source agent skills for authors who want to critique, proofread, plan translation, or prepare production requirements for a manuscript in their own AI workspace.

The pack contains four independently installable skills:

- **Full Manuscript Roast** builds a manuscript map, reviews every chapter in context, verifies quoted evidence, prioritizes problems, and produces an editorial revision plan.
- **Full Manuscript Proofreading** checks the complete book chapter by chapter, separates objective corrections from optional style notes, maintains a consistency sheet, and produces an auditable correction report.
- **Literary Translation (BETA)** inventories translation ambiguities, records explicit author decisions, and maintains the decision/glossary foundation. It is not yet an end-to-end translation engine.
- **Book Production (BETA)** collects destination requirements and defines print/ebook preflight checks. It does not yet generate or certify upload-ready KDP/EPUB files.

The skills are orchestration instructions and offline local workflow tools. They are not an AI model, a hosted editing service, or a promise that an arbitrary chat window can process an unlimited book in one message. No live LLM call is implemented in this repository.

## Why they are free

EditRoast publishes these tools so independent authors can run a careful, inspectable editing workflow on their own computer. There is no registration, email gate, or payment to download or use the pack.

Your AI provider may still charge for a subscription or API usage, and every provider has its own context, file, message, and rate limits. The included Python workflows use only the standard library, have no paid dependency, and do not require an API key.

## Subscription-first usage planning (ChatGPT Plus and Claude Pro)

The primary supported agent path is **Codex signed in with ChatGPT Plus** or **Claude Code signed in with Claude Pro** (each advertised at US **$20/month**). The open-source skills themselves are free. **An API key is not required** when using an eligible agent subscription, but the plan does **not** include unlimited model use or a guaranteed number of books per billing period. Long manuscripts may require multiple sessions and waiting for five-hour or weekly usage limits to reset. Do not turn on optional pay-as-you-go credits or API billing as an automatic fallback.

For planning only, a 90,000-word English manuscript is roughly 120k–140k source tokens. A **hypothetical four-stage** roast → proofreading → literary translation → book production workflow was estimated at **~1.28M–2.17M total model input/output tokens**. **This is not benchmarked, not a subscription-token quota, and not the usage of any shipping end-to-end pipeline.** Roast and proofreading workflows exist; full literary translation and publish-ready book production remain BETA/roadmap.

See the [90k-word subscription capacity model, multilingual chunking assumptions and measurement plan](docs/SUBSCRIPTION-CAPACITY-PLANNING.md). Always report actual provider limits and observed usage separately from estimates.

## What stays local

The preparation, chapter splitting, progress state, evidence ledger, and validation steps run locally. Your manuscript is not uploaded to EditRoast.

When you ask an AI product to read a manuscript, that product receives the text you give it under its own privacy and data-retention terms. Review those terms before using confidential or unpublished work. Do not commit manuscripts, API keys, or generated workspaces to Git.

## Quick start

Requirements: Python 3.10 or newer and either Codex or Claude Code. TXT, Markdown, and DOCX are supported. EPUB is intentionally not included in v1.

1. Download and unzip a release.
2. Open a terminal in the extracted folder.
3. Install one or all skills into a project:

```bash
python install.py --target codex --scope project --skill all --project-dir /path/to/your-book-project
```

For Claude Code, replace `codex` with `claude`.

4. Put the manuscript anywhere inside that project, open the project in your agent, and ask:

```text
Use $manuscript-roast to critique my complete manuscript at manuscript.docx.
```

or in Claude Code:

```text
/manuscript-roast Critique my complete manuscript at manuscript.docx.
```

The agent will create a local workspace with separate `input/`, `work/`, and `output/` folders. The original file is copied without modification.

See [Codex installation](docs/INSTALL-CODEX.md), [Claude Code installation](docs/INSTALL-CLAUDE.md), or [manual chat workflow](docs/INSTALL-CHATGPT.md) for exact steps.

## A book of any length

There is no hard-coded word limit. Long books are split into stable, line-addressable chunks. The workflow first inventories the whole manuscript, then builds a global book map, then reviews each chunk while carrying forward that map and the accumulated findings.

The practical limit is your AI provider's usage allowance and how long you are willing to let the multi-pass review run. A longer manuscript means more model turns and potentially more provider cost.

## Resume an interrupted v1 workflow

Each completed chunk is recorded in `work/state.json`. To inspect progress:

```bash
python .agents/skills/manuscript-roast/scripts/manuscript_workflow.py status --workspace editroast-work/my-book
```

The status command reports completed, pending, and stale report files. The agent continues from the first pending chunk instead of starting over.

The durable project engine also provides ER-014 revision-bound runs for agent integrations. It records each chapter or paragraph as a separate work unit, issues a fresh attempt token when work is claimed, and accepts completion only with a non-empty project-local result. A stopped process can resume without repeating verified completed units:

```bash
python scripts/editroast.py run create ./my-book roast-2026-10-28 r000002 --type manuscript-roast --scope chapter
python scripts/editroast.py run claim ./my-book roast-2026-10-28 --worker codex-session-1
python scripts/editroast.py run checkpoint ./my-book roast-2026-10-28 --status blocked_by_usage_limit --reason "Provider allowance reached"
python scripts/editroast.py run resume ./my-book roast-2026-10-28
python scripts/editroast.py run verify ./my-book roast-2026-10-28
```

`run complete` requires the claimed unit ID, its exact attempt token, and a result file inside the project. Coverage reaches 100% only while every recorded result still exists and matches its SHA-256. This engine stores orchestration state; it does not call a model or claim that an editorial judgment is correct.

## Durable manuscript projects, revision diff, safe apply, and resumable runs (alpha)

The ER-010 through ER-014 project engine keeps original imports as immutable, SHA-256-verified revisions and stores extracted structure, findings, decisions, structural diffs, reconciliation, correction approvals, apply history, and run checkpoints in project-local SQLite. From a source checkout:

```bash
python scripts/editroast.py project create ./my-book --title "My Book" --language en
python scripts/editroast.py manuscript import ./my-book manuscript.docx
python scripts/editroast.py manuscript status ./my-book
python scripts/editroast.py manuscript revisions ./my-book
python scripts/editroast.py manuscript show ./my-book r000001
python scripts/editroast.py manuscript diff ./my-book r000002
python scripts/editroast.py manuscript reconcile-list ./my-book r000002
python scripts/editroast.py manuscript verify ./my-book
```

The diff reports chapter and paragraph moves, changes, splits, merges, additions, removals, and unresolved candidates. Manuscript text is omitted by default; add `--include-text` only in a trusted local terminal. Resolve a listed candidate with `manuscript reconcile`, and inspect its append-only history with `manuscript reconcile-history`.

ER-013 can stage exact correction spans, record author decisions, preview an apply without writing, and create a new immutable revision only after the author confirms the preview hash:

```bash
python scripts/editroast.py manuscript correction-add ./my-book r000002 C-001 --paragraph <stable-id> --before "teh" --after "the" --class mechanical
python scripts/editroast.py manuscript correction-decide ./my-book approved --id C-001 --confirm-author-approval
python scripts/editroast.py manuscript apply-preview ./my-book r000002 --id C-001
python scripts/editroast.py manuscript apply ./my-book r000002 --id C-001 --plan-hash <preview-plan-hash> --confirm-author-approval
```

Preview is mandatory. Apply refuses stale or modified sources, overlapping spans, unresolved paragraph mappings, unapproved corrections, and unsafe DOCX run/formatting boundaries. Rollback is also previewed and creates another revision; it never overwrites or deletes the original.

Inside an installed skill, use the same commands with `<skill>/scripts/editroast.py`. Developers can also set `PYTHONPATH=src` and run `python -m editroast_core`. See [durable manuscript state](docs/MANUSCRIPT-STATE.md) for the directory layout, schema, migration, paragraph identity, and reconciliation rules.

## Verifiable evidence

Findings use stable chunk IDs and line ranges. The validator checks that:

- every manuscript chunk is accounted for;
- every completed chunk has a report;
- every evidence record points to an existing chunk and valid line range;
- every quoted passage matches the source text after whitespace normalization;
- the final report exists only after the full run is complete.

The validator can catch missing work and invented or miscopied quotes. It cannot decide whether an editorial judgment is good; the author remains the final decision-maker.

## Optional API use

Codex and Claude Code can use the account or provider configured in those products. No API key is required by this repository. Advanced users may run their agent through a separately configured API account, but keys must remain in environment variables or the provider's credential store—never in this repo, a prompt, or a manuscript workspace.

## How this works under the hood

EditRoast is **not just a reusable prompt**: it combines agent-level editorial instructions with maintained offline Python tools for manuscript preparation, persistent chunk progress, source-grounded evidence validation, reproducible ZIP builds and installation checks. As of PR-01 there are **706 lines of canonical Python Core, 525 lines of installer/CLI/build/security tooling, and 315 lines of tests** (physical lines including blanks; [measurement scope and source commit](docs/UNDER-THE-HOOD.md)).

Read the [detailed engineering explainer](docs/UNDER-THE-HOOD.md) for executable architecture, data flow, security boundaries, exact implementation status, test methodology and limitations. Contributors and coding agents should also read [AGENTS.md](AGENTS.md). These are verifiable engineering facts, **not** a claim that full AI-powered book rewriting, translation or print-ready publishing have shipped.

## Repository map

```text
skills/       Agent Skills source
scripts/      Shared local workflow and release builder
src/          Single canonical editroast_core source module
templates/    Book map and report templates
examples/     Synthetic manuscript and example reports
tests/        Workflow tests
docs/         Installation and manual-chat guides
dist/         Versioned release ZIP files (generated)
```

The `skills/` folders are concise source specifications. `scripts/build_release.py` deterministically vendors `src/editroast_core/`, compatibility entry points, and templates into each standalone artifact. Runtime copies inside built packages are generated, not maintained independently.

## Build and verify

```bash
python scripts/build_release.py
python scripts/security_check.py
python -m unittest discover -s tests -v
```

The builder creates four individual archives, a complete pack, and `dist/SHA256SUMS.txt`. Tests rebuild in isolated temporary directories, compare hashes for determinism, install built artifacts into temporary Codex and Claude Code projects, and check required bundled dependencies.

## Known limitations

- The workflow supports TXT, Markdown, and DOCX. Safe apply does not create or preserve Word tracked-change markup and does not promise layout equivalence.
- Durable ingest preserves DOCX source bytes and mixed run/style metadata. Safe apply is deliberately limited to exact spans inside one simple text run; it refuses cross-run or control-node edits. Unmodified archive members are copied, but `word/document.xml` is serialized again, so the result is not byte-identical and complex Word behavior remains outside the guarantee.
- Paragraph matching is intentionally conservative. Ambiguous repeated or changed fragments receive new IDs and ranked candidates rather than being silently linked; manual reconciliation records a relationship but does not rewrite an immutable revision.
- Name, number, capitalization, and negation differences are lexical review flags, not proof of semantic equivalence, authorial intent, or factual error.
- ER-013 applies only explicitly approved, exact-span corrections after a successful preview. It does not generate corrections from editorial findings or infer safe semantic edits automatically.
- The v1 editorial workflow still produces Markdown reports. The alpha durable-project CLI is a separate, conservative approval/apply foundation and never silently rewrites a source manuscript.
- Corrected DOCX output is limited to simple supported runs; formatting collisions are blocked for manual resolution rather than flattened or guessed.
- Translation and book production are BETA foundations only; branded translation-decision PDFs, full translation, DOCX/EPUB/PDF generation, and platform acceptance checks remain unsupported.
- The quality and language coverage of the editorial work depend on the selected model.
- Ordinary browser chat products may not provide command execution, persistent files, or enough context for the automated workflow. Use the manual guide instead.

## Contributing and support

Use [GitHub Issues](https://github.com/ELForestBali/editroast-open-skills/issues) to report a bug or suggest an improvement. Include a minimal synthetic example, your operating system, Python version, and the validation output. Never attach a confidential manuscript or API key.

## License

MIT. See [LICENSE](LICENSE).

