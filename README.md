# EditRoast Open Skills

Free, open-source agent skills for authors who want to critique or proofread an entire manuscript with their own AI workspace.

The pack contains two skills:

- **Full Manuscript Roast** builds a manuscript map, reviews every chapter in context, verifies quoted evidence, prioritizes problems, and produces an editorial revision plan.
- **Full Manuscript Proofreading** checks the complete book chapter by chapter, separates objective corrections from optional style notes, maintains a consistency sheet, and produces an auditable correction report.

The skills are orchestration instructions and local workflow tools. They are not an AI model, a hosted editing service, or a promise that an arbitrary chat window can process an unlimited book in one message.

## Why they are free

EditRoast publishes these tools so independent authors can run a careful, inspectable editing workflow on their own computer. There is no registration, email gate, or payment to download or use the pack.

Your AI provider may still charge for a subscription or API usage, and every provider has its own context, file, message, and rate limits. The included Python workflow itself has no paid dependency and does not require an API key.

## What stays local

The preparation, chapter splitting, progress state, evidence ledger, and validation steps run locally. Your manuscript is not uploaded to EditRoast.

When you ask an AI product to read a manuscript, that product receives the text you give it under its own privacy and data-retention terms. Review those terms before using confidential or unpublished work. Do not commit manuscripts, API keys, or generated workspaces to Git.

## Quick start

Requirements: Python 3.10 or newer and either Codex or Claude Code. TXT, Markdown, and DOCX are supported. EPUB is intentionally not included in v1.

1. Download and unzip a release.
2. Open a terminal in the extracted folder.
3. Install one or both skills into a project:

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

## Resume an interrupted run

Each completed chunk is recorded in `work/state.json`. To inspect progress:

```bash
python .agents/skills/manuscript-roast/scripts/manuscript_workflow.py status --workspace editroast-work/my-book
```

The status command reports completed, pending, and stale report files. The agent continues from the first pending chunk instead of starting over.

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

## Repository map

```text
skills/       Agent Skills source
scripts/      Shared local workflow and release builder
templates/    Book map and report templates
examples/     Synthetic manuscript and example reports
tests/        Workflow tests
docs/         Installation and manual-chat guides
dist/         Versioned release ZIP files (generated)
```

## Known limitations

- The workflow supports TXT, Markdown, and DOCX. It does not preserve DOCX tracked changes or layout.
- v1 produces Markdown reports. It does not silently rewrite the source manuscript.
- A corrected DOCX is not generated in v1 because reliable round-trip editing and change verification vary too much across documents.
- The quality and language coverage of the editorial work depend on the selected model.
- Ordinary browser chat products may not provide command execution, persistent files, or enough context for the automated workflow. Use the manual guide instead.

## Contributing and support

Use [GitHub Issues](https://github.com/ELForestBali/editroast-open-skills/issues) to report a bug or suggest an improvement. Include a minimal synthetic example, your operating system, Python version, and the validation output. Never attach a confidential manuscript or API key.

## License

MIT. See [LICENSE](LICENSE).

