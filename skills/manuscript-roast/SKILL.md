---
name: manuscript-roast
description: Critique a complete fiction or narrative nonfiction manuscript from TXT, Markdown, or DOCX, including books longer than one context window. Use for full-book developmental feedback, plot and character analysis, chapter-by-chapter critique, continuity checks, or a prioritized revision plan. Do not use for proofreading-only requests or for silently rewriting the author's prose.
license: MIT
metadata:
  author: EditRoast
  version: "2.0.0-rc.1"
  status: "stable-v1-workflow"
---

> **Implementation boundary:** This skill combines instructions to the user's AI agent with bundled, executable offline Python tools. Local code handles file preparation, source chunk IDs, saved progress, evidence validation, whole-book maps, Editorial Board audit state, author-approved exact corrections, and comparative re-check checkpoints; the AI agent supplies editorial judgment. Do not claim that the code itself runs a language model or proves literary quality. See [technical architecture](https://github.com/ELForestBali/editroast-open-skills/blob/main/docs/UNDER-THE-HOOD.md) and [current feature status](https://editroast.com/skills/under-the-hood/). Complete literary translation, branded PDFs, and production-ready KDP/EPUB exports remain separate BETA work.

# Full Manuscript Roast

Produce a candid, evidence-backed editorial diagnosis of the entire manuscript. Preserve the author's voice and agency: recommend revisions, but do not silently rewrite the book.

The bundled offline preparation and validation CLI requires Python 3.10 or newer.

Use the stable role IDs, historical display names, specialties, and report order in [references/editorial-personas.md](references/editorial-personas.md) when presenting the three editorial lenses and Editorial Board. These are virtual roles, not human editors.

## Before starting

- Confirm the manuscript path from the user's supplied file or project context. Ask only if no source can be identified.
- Treat the manuscript as confidential. Keep the workspace local and never send it to EditRoast.
- Do not print, log, commit, or request an API key. The workflow script makes no network calls.
- Supported inputs are TXT, Markdown, and DOCX. Explain that EPUB is not supported in v1.

## Prepare or resume

Locate `scripts/manuscript_workflow.py` inside this skill directory.

For a new run, choose a short filesystem-safe book slug and run:

```text
python scripts/manuscript_workflow.py prepare <manuscript> --workspace editroast-work/<book-slug> --mode roast --max-words 5000
```

If the workspace already exists, do not replace it. Run:

```text
python scripts/manuscript_workflow.py status --workspace editroast-work/<book-slug>
```

Read `work/manifest.json`, `work/state.json`, and `work/book-map.md`. Continue from pending chunks. Never mark a chunk complete before its report exists.

## Analyze in four passes

### 1. Inventory the whole book

Skim every chunk before making book-level claims. Fill `work/book-map.md` using `templates/book-map.md` as the structure. Track:

- premise, genre, intended reader experience, and narrative mode;
- chapter purpose and change;
- characters, goals, motivations, relationships, and arc state;
- plot and subplot events, chronology, locations, promises, setups, and payoffs;
- POV, knowledge boundaries, rules, names, and terms;
- early questions that later chapters may answer.

Do not mistake an unresolved setup for a plot hole until the entire manuscript has been inventoried.

### 2. Review every chunk in global context

Create `work/chapter-reports/<chunk-id>.md` from the chapter-report template. Evaluate the chunk against the current book map:

- causality and plot progression;
- character motivation and arc movement;
- scene goal, conflict, turn, and consequence;
- pacing, exposition, dialogue, POV, tone, and reader orientation;
- continuity, chronology, spatial logic, and factual consistency;
- strong scenes worth protecting and weak scenes needing revision.

Classify issues as `critical`, `major`, or `minor`. Diagnose before prescribing. Explain the consequence for the reader and give a revision direction without replacing the author's prose.

### 3. Record exact evidence

Append one JSON object per quoted finding to `work/evidence.jsonl`:

```json
{"finding_id":"R-001","chunk_id":"ch001-p001","start_line":12,"end_line":14,"quote":"Exact words from the numbered source lines","severity":"major"}
```

Copy quotes exactly from the selected numbered lines, excluding the number prefix. Use longer anchors when a short quote repeats. Never invent or reconstruct a quote from memory.

After saving a chunk report, mark it complete:

```text
python scripts/manuscript_workflow.py complete --workspace editroast-work/<book-slug> --chunk-id <chunk-id> --report editroast-work/<book-slug>/work/chapter-reports/<chunk-id>.md
```

### 4. Synthesize only after full coverage

When every chunk is complete, create `output/manuscript-roast-report.md` from `templates/final-roast-report.md`.

The final report must:

- state a direct editorial verdict without automatic praise;
- separate evidence from inference;
- identify the strongest and weakest scenes with precise locations;
- consolidate repeated symptoms under root causes;
- prioritize structural blockers before prose polish;
- order the revision plan by dependency so later work is not invalidated;
- name unresolved uncertainty instead of inventing facts;
- include the actual chapter and chunk coverage.

Run the final gate:

```text
python scripts/manuscript_workflow.py validate --workspace editroast-work/<book-slug> --require-final
```

Do not declare completion if validation fails. Fix missing coverage, stale reports, or inaccurate evidence and rerun it.

## Quality rules

- Distinguish author, narrator, and character.
- Do not infer identity, intent, trauma, politics, or autobiography without textual support.
- Do not impose a formula when the manuscript makes a coherent deliberate choice.
- Avoid minimum finding counts. A clean chapter may be marked clean.
- Prefer a few consequential, well-supported findings over a long generic checklist.
- Analyze in the manuscript's language when the selected model can do so reliably; otherwise disclose the limitation.

## Durable Editorial Board workflow

When the project uses the durable `scripts/editroast.py` workflow, build the
ER-020 two-pass map first and run Leo Shelf, Vera Sharp and Boris Marker as
three independent ER-014 chapter runs. Chief Sterling chairs the synthesis;
do not invent a fourth independent review. Preserve disagreement and exact
revision/paragraph/quotation evidence.

Record the explicit chair decision with `editorial record`. The author may
approve, reject or defer each canonical group. Never infer approval. A first
approval of a revision group requires an exact change JSON; semantic changes
need the extra semantic-group confirmation. Use `editorial apply-preview` and
the returned plan hash before `editorial apply`. These commands delegate to
ER-013 and must not be bypassed. Use previewed rollback if the author reverses
an applied batch.

After changes, use `editorial recheck-start` on the reconciled descendant
revision. This is one resumable Editorial Board comparison, not three new
detailed reviews. Classify exact-source findings as improved, unresolved or
new, then finalize one verdict. Deliver `editorial report --format markdown`
and/or its structured JSON in this order: verdict → three editors → board
decision → unified correction list → prioritized revision plan. Report source
integrity separately from editorial quality.

## Handoff

Return the final report path, validation result, coverage count, and any limitations. Mention that the source file remains unchanged and the author decides which recommendations to accept.

