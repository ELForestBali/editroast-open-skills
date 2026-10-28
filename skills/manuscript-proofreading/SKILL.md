---
name: manuscript-proofreading
description: Proofread a complete book from TXT, Markdown, or DOCX across multiple chapters while preserving the author's voice. Use for spelling, grammar, punctuation, terminology, name, timeline, and formatting consistency checks, including books longer than one context window. Do not use for developmental critique or silent wholesale rewriting.
license: MIT
metadata:
  author: EditRoast
  version: "2.0.0-alpha.1"
  status: "stable-v1-workflow"
---

# Full Manuscript Proofreading

Audit the entire manuscript for correctness and consistency while preserving its voice. Report proposed changes visibly. Do not silently alter the original.

The bundled offline preparation and validation CLI requires Python 3.10 or newer.

## Before starting

- Identify the manuscript path from supplied files or project context. Ask only if no source can be identified.
- Determine the manuscript language and likely dialect from the text and user context. State uncertainty rather than pretending.
- Treat the manuscript as confidential. Keep the workspace local and never send it to EditRoast.
- Do not print, log, commit, or request an API key. The workflow script makes no network calls.
- Supported inputs are TXT, Markdown, and DOCX. EPUB and tracked-change DOCX output are outside v1.

## Prepare or resume

Locate `scripts/manuscript_workflow.py` inside this skill directory.

For a new run:

```text
python scripts/manuscript_workflow.py prepare <manuscript> --workspace editroast-work/<book-slug> --mode proofreading --max-words 5000
```

If the workspace exists, preserve it and run:

```text
python scripts/manuscript_workflow.py status --workspace editroast-work/<book-slug>
```

Read `work/manifest.json` and `work/state.json`. Create `work/style-sheet.md` from `templates/style-sheet.md` and carry it through the full book.

## Establish the editing basis

Before the detailed pass, inventory every chunk and record:

- language and dialect;
- dialogue punctuation and quotation convention;
- names, titles, places, invented terms, capitalization, and hyphenation;
- tense, POV, measurements, numerals, dates, and typography;
- deliberate fragments, slang, code-switching, and other voice features to preserve;
- uncertainties that need author confirmation.

Use a recognized dictionary or style authority only when available in the environment. Do not claim an external source was checked when it was not.

## Proofread every chunk

Create `work/chapter-reports/<chunk-id>.md` for each chunk. Review in source order and update the style sheet after every chapter.

Separate findings into:

1. **Required corrections** — spelling, grammar, punctuation, agreement, unmistakable word errors, broken formatting, and established cross-book inconsistencies.
2. **Optional style suggestions** — clarity, awkward phrasing, repetition, rhythm, or idiom where more than one valid choice exists.
3. **Needs author decision** — ambiguous facts, deliberate voice, dialect, or continuity where the text does not establish the intended form.

For each proposed change include a stable ID, exact location, exact original, proposed correction or question, rule/category, and a short reason. Never manufacture findings to meet a quota. Record a clean chunk as reviewed with no required corrections.

## Record exact evidence

Append one JSON object per quoted finding to `work/evidence.jsonl`:

```json
{"finding_id":"P-001","chunk_id":"ch001-p001","start_line":8,"end_line":8,"quote":"Exact words from the numbered source line","severity":"required"}
```

Copy the quote exactly, excluding the line-number prefix. The validator accepts normalized whitespace but not invented or paraphrased text.

After saving a chunk report, mark it complete:

```text
python scripts/manuscript_workflow.py complete --workspace editroast-work/<book-slug> --chunk-id <chunk-id> --report editroast-work/<book-slug>/work/chapter-reports/<chunk-id>.md
```

## Synthesize the audit

After every chunk is complete, create `output/proofreading-report.md` from `templates/proofreading-report.md`. Keep required corrections and optional suggestions separate. Include cross-chapter consistency decisions, clean chapters, unresolved author questions, and actual coverage.

Do not create a corrected DOCX in v1. Explain that round-tripping complex DOCX structure and reviewable tracked changes is not reliable enough for a silent automated edit. The report is the auditable deliverable.

Run:

```text
python scripts/manuscript_workflow.py validate --workspace editroast-work/<book-slug> --require-final
```

Do not declare completion until validation passes.

## Voice-preservation rules

- Do not normalize dialect, slang, fragments, repetition, or unconventional punctuation merely because they are unconventional.
- Correct an unusual proper name or fact only when the manuscript or a verified source establishes the intended form; otherwise mark it for author review.
- Do not turn concise or idiosyncratic prose into generic polished AI prose.
- Do not apply a correction globally without checking every occurrence in context.
- Do not conflate consistency with sameness: dialogue voices may legitimately differ.

## Handoff

Return the report path, style-sheet path, validation result, coverage count, and limitations. Remind the author that the source is unchanged and all edits remain reviewable.

