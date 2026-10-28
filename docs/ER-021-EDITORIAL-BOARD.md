# ER-021 alpha: source-grounded three-lens editorial board preview

Status: **first executable validation/assembly slice, not the completed editorial workflow**.
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

### Work remaining before ER-021 is complete

1. Independent lens-specific agent instructions and ER-014-backed resumable
   review runs; no parallel checkpoint system.
2. Explicit editorial-board adjudication and cross-lens semantic root-cause
   reconciliation, with inspectable disagreements and a final verdict.
3. Single actionable correction list integrated with author approvals,
   dependency/conflict resolution and ER-013's preview/apply gates.
4. One comparative Board Re-check on a revised manuscript, using verified
   reconciliation, not three new full reviews by default.
5. EN/RU private-beta quality gates distinct from source/CI integrity tests.
