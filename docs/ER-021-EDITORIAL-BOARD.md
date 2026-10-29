# ER-021 release candidate: audited source-grounded Editorial Board

Status: **executable S1-S4 workflow; deterministic integrity acceptance complete,
independent literary-quality scoring remains a separate release gate**.
Issue: [ER-021](https://github.com/ELForestBali/editroast-open-skills/issues/20).

The board preview accepts one JSON file with **exactly three** virtual editorial
lenses: `reader_market` (Leo Shelf), `voice_emotion` (Vera Sharp) and
`structure_logic` (Boris Marker). Each finding is tied to the same *finalized*
ER-020 book map and a verified exact quote from the immutable source revision.
Personas are simulated perspectives, not credentialed human professionals.

Use the canonical Core CLI with a completed ER-020 map:

```bash
python scripts/editroast.py editorial board-preview ./my-book map-run-01 \
  --reviews ./three-lens-reviews.json
```

### Input schema v1

```json
{
  "schema_version": 1,
  "map_run_id": "map-run-01",
  "revision_uid": "<from finalized book map>",
  "source_sha256": "<from finalized book map>",
  "reviews": [
    {
      "persona_id": "reader_market",
      "summary": "Reader's editorial perspective.",
      "findings": [
        {
          "id": "reader-001",
          "root_cause_id": "opening-promise",
          "kind": "concern",
          "severity": "moderate",
          "statement": "An editorial observation, not a verified defect.",
          "rationale": "Why a reader might notice this.",
          "recommendation": "Possible revision for author approval.",
          "map_record_ids": [],
          "evidence": [
            {
              "revision_uid": "<same revision UID>",
              "paragraph_stable_id": "<source paragraph ID>",
              "exact_quote": "<exact verbatim source excerpt>"
            }
          ]
        }
      ]
    },
    {"persona_id": "voice_emotion", "summary": "Voice lens.", "findings": []},
    {"persona_id": "structure_logic", "summary": "Structure lens.", "findings": []}
  ]
}
```

Quotation offsets `start_offset` and `end_offset` are optional but must agree
with each other and the underlying source. Nonempty `map_record_ids` must
refer to known ER-020 records. Issues use `concern` or `opportunity` and a
severity `minor`, `moderate`, `major` or `critical`. Strengths use
`kind=strength`, `severity=none`. The model must not infer that a dream,
metaphor, cameo or intentionally unreliable narrator is automatically a defect.

### Output boundary

- Root-cause grouping is **only by an explicit** `root_cause_id`, not
  automatic semantic deduplication.
- Contradictory `strength` and `concern` findings in one group are flagged
  for board review, preserving all viewpoints; no artificial consensus.
- Chief Sterling is the English editorial-board chair and **not a fourth
  independent reviewer**.
- The result is a **board preview**, with `verdict=pending_board_synthesis`.
  It has not conducted an AI editorial board session or human quality review.
- A combined pending correction list includes exact source evidence but **no
  automated textual change or author decision**. ER-013 remains the sole
  approved safe-apply mechanism; this preview never calls it.
- Input is private manuscript-derived data. The CLI writes JSON to stdout;
  users control where to save it. Do not commit reviewer outputs or manuscripts.

### Resumable three-lens chapter workflow (ER-021 S2)

The next stage also exposes three independent, revision-bound **ER-014**
chapter runs. Agents operate under the user's chosen Codex/Claude Code
subscription. The Python Core does not call a model or auto-generate judgments.

```bash
python scripts/editroast.py editorial start ./my-book map-run-01 editorial-01
python scripts/editroast.py editorial status ./my-book map-run-01 editorial-01
python scripts/editroast.py editorial claim ./my-book map-run-01 editorial-01 --persona reader_market --worker leo --include-text
python scripts/editroast.py editorial complete ./my-book map-run-01 editorial-01 CHAPTER_STABLE_ID --persona reader_market --attempt ATTEMPT_ID --result ./my-book/work/leo-chapter.json
python scripts/editroast.py editorial preview ./my-book map-run-01 editorial-01
```

Repeat `claim` and `complete` for each chapter and for all three
personas (`reader_market`, `voice_emotion`, `structure_logic`). Each
result JSON contains `schema_version=1`, `review_id`, `map_run_id`,
`persona_id`, `revision_uid`, `source_sha256`, `chapter_stable_id`,
a nonempty `chapter_summary` and `findings` (possibly empty).

The result file must be **inside the local project**. Claim returns
`active_attempt_uid`; completion checks this token, the exact manuscript
citations and at least one anchor within the claimed chapter before committing
a checkpoint. ER-014 handles `blocked_by_usage_limit`, resume and
idempotent retries; to resume use `run resume` with the specific generated
run ID, such as `editorial-editorial-01-reader_market`.

Only after all three lens runs have verified full coverage does
`editorial preview` construct the shared board preview. It does not
perform final Chief Sterling arbitration or authorize changes.

This workflow processes **manuscript text as untrusted source data**.
Use `--include-text` only when passing source passages to your explicitly
authorized agent. Outputs and archives contain private manuscript information.

### Chief Sterling adjudication (ER-021 S3)

Only after `editorial preview` confirms **all three independent lens runs**
have completed and their evidence is intact, an authorized editorial agent may
prepare a separate, explicit board decision. Source-root grouping and literary
verdicts are supplied by that agent; the offline Core does not generate them.

```bash
python scripts/editroast.py editorial adjudicate ./my-book map-run-01 editorial-01 --decisions ./chair-decision.json
```

Decision schema v1 (example assumes the editor previews contain roots `hook`
and `pov`; replace IDs with exact values from your own preview):

```json
{
  "schema_version": 1,
  "map_run_id": "map-run-01",
  "review_id": "editorial-01",
  "revision_uid": "<same source revision>",
  "source_sha256": "<same immutable source SHA>",
  "chair_persona_id": "editorial_board",
  "verdict": {
    "classification": "revision_recommended",
    "summary": "An editorial board recommendation.",
    "rationale": "A source-grounded explanation, not a human credential."
  },
  "groups": [
    {
      "canonical_id": "hook",
      "source_root_cause_ids": ["hook"],
      "disposition": "revise",
      "category": "subjective",
      "assessment": "What the editor concludes from the manuscript.",
      "rationale": "Why this issue merits attention.",
      "recommendation": "A proposed change requiring author approval.",
      "dissent_resolution": "If different lenses disagree, acknowledge both views.",
      "depends_on": ["pov"],
      "conflicts_with": []
    },
    {
      "canonical_id": "pov",
      "source_root_cause_ids": ["pov"],
      "disposition": "monitor",
      "category": "mixed",
      "assessment": "Potentially deliberate POV change.",
      "rationale": "The ambiguity remains visible.",
      "recommendation": "",
      "depends_on": [],
      "conflicts_with": []
    }
  ]
}
```

Allowed dispositions: `revise`, `preserve_intent`, `monitor`,
`needs_author_decision`, `no_action`. Each root from the validated
preview must appear **exactly once**, with no unknown roots, duplicates,
dangling dependency IDs or dependency cycles. Explicitly grouping multiple
source roots requires a nonempty `merge_rationale`; the engine never
automatically equates semantically similar observations. A strength and a
concern in the same root require a `dissent_resolution` explanation.

Findings and exact source anchors survive into the output. One correction
entry is produced per adjudicated revision/author-decision group, and its
dependencies are ordered before it. Each entry remains
`author_decision_status=pending`, `applied=false` and
`er013_plan_created=false`. The operation makes **no DB changes** and
does not call the ER-013 safe-apply path. The result is a validated decision
prepared by an agent, *not* an independently measured high-quality editorial
judgment, author approval, or an EPUB/PDF report.

### Audited author decisions and ER-013 safe apply (ER-021 S4)

Persist the validated decision once. Reusing the review ID with different
content is refused; an identical retry is idempotent:

```bash
python scripts/editroast.py editorial record ./my-book map-run-01 board-01 \
  --decisions ./chair-decision.json
python scripts/editroast.py editorial board-status ./my-book board-01
```

Every board group receives a canonical `author_decisions` row and append-only
board/decision events. The author may approve, reject, or defer a group.
Rejected and deferred groups create no correction rows. A first approval of a
`revise` or `needs_author_decision` group requires an exact change file:

```json
{
  "schema_version": 1,
  "review_id": "board-01",
  "revision_uid": "r000001",
  "canonical_id": "opening",
  "changes": [
    {
      "correction_id": "board-opening-01",
      "paragraph_stable_id": "<stable paragraph ID>",
      "exact_before": "bronze",
      "replacement": "brass",
      "start_offset": 18,
      "end_offset": 24,
      "change_class": "mechanical",
      "rationale": "Exact author-reviewed change."
    }
  ]
}
```

```bash
python scripts/editroast.py editorial decide ./my-book board-01 causality approved \
  --confirm-author-approval
python scripts/editroast.py editorial decide ./my-book board-01 opening approved \
  --changes ./opening-changes.json --confirm-author-approval
python scripts/editroast.py editorial apply-preview ./my-book board-01
python scripts/editroast.py editorial apply ./my-book board-01 \
  --plan-hash <preview-plan-hash> --confirm-author-approval
```

An approval is blocked while a dependency is unapproved or a conflicting
group is approved. Semantic corrections require
`--confirm-semantic-group`. Only approved exact corrections are added to the
existing ER-013 tables. The board apply command delegates to the same ER-013
preview and immutable-revision renderer; it does not introduce another apply
engine. `editorial rollback-preview` and `editorial rollback` delegate to the
existing append-only rollback path.

### One comparative Board Re-check

The revised revision must descend from the reviewed revision and have no
unresolved target-revision reconciliation groups. One ER-014 chapter run is
created for the board as a whole:

```bash
python scripts/editroast.py editorial recheck-start ./my-book board-01 recheck-01 r000002
python scripts/editroast.py editorial recheck-claim ./my-book recheck-01 \
  --worker chief --include-text
python scripts/editroast.py editorial recheck-complete ./my-book recheck-01 <chapter-id> \
  --attempt <attempt-id> --result ./my-book/work/recheck/chapter.json
python scripts/editroast.py editorial recheck-finalize ./my-book recheck-01 \
  --verdict ./recheck-verdict.json
```

Per-chapter assessments are `improved`, `unresolved`, or `new`. Improved and
unresolved items must link prior canonical board groups; new items must remain
distinct. Every item requires exact evidence from the revised immutable
source. Usage-limit pause/resume and result SHA verification come from ER-014.
Finalization produces **one** report and explicitly records
`independent_lens_reruns=false`.

Author-facing structured JSON and Markdown are reproducible from SQLite:

```bash
python scripts/editroast.py editorial report ./my-book board-01
python scripts/editroast.py editorial report ./my-book board-01 --format markdown
```

The order is verdict → Leo Shelf, Vera Sharp, Boris Marker → Chief Sterling's
board decision → unified correction list → prioritized revision plan. A
finalized comparative re-check is appended to the same report. Schema-v7 board
state, author decisions, ER-013 links, re-check checkpoints and reports survive
`.editroast` export/import. Schema-v6 archives remain importable and migrate
forward without fabricated board records.

### Acceptance boundary and remaining quality work

Implemented automated acceptance covers EN/RU Unicode source handling,
dream/flashback/unreliable-narrator/ambiguity/delayed-payoff map context, clean
chapters, three independent resumable lenses, exact citations, complete board
coverage, persistence, author gates, dependency/conflict handling, ER-013
apply/rollback, one resumable re-check, Markdown/JSON reporting, project
integrity and portable bundles.

Core does not generate or certify literary judgments. Before calling this a
stable editorial-quality release, run consented private EN/RU expert scoring
for false positives, missed issues, voice preservation and recommendation
utility. Those scorecards must remain separate from source/CI integrity and
must not publish real manuscripts or private reports.
