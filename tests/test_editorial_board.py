"""Synthetic ER-021 editorial board preview tests; no real manuscripts used."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.book_map import (
    advance_book_map, claim_book_map_unit, complete_book_map_unit,
    finalize_book_map, start_book_map,
)
from editroast_core.board_synthesis import adjudicate_editorial_board
from editroast_core.editorial_board import (
    preview_editorial_board, start_editorial_lenses, editorial_lenses_status,
    claim_editorial_lens_unit, complete_editorial_lens_unit,
    preview_completed_editorial_lenses,
)
from editroast_core.manuscript_state import ManuscriptProject


class EditorialBoardPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "synthetic.md"
        self.source.write_text("# One\nMara returned the bronze key.\n", encoding="utf-8")
        self.project = ManuscriptProject.create(self.root / "project", language="en")
        self.revision = self.project.import_manuscript(self.source)["revision_uid"]
        self.map_id = "editorial-synthetic"
        start_book_map(self.project, self.revision, self.map_id)
        for stage in ("A", "B"):
            if stage == "B":
                advance_book_map(self.project, self.map_id)
            claimed = claim_book_map_unit(
                self.project, self.map_id, stage, worker_id="fixture", include_text=True
            )
            unit = claimed["next_unit"]
            self.assertIsNotNone(unit)
            payload = {
                "schema_version": 2,
                "map_run_id": self.map_id,
                "pass": stage,
                "revision_uid": self.revision,
                "chapter_stable_id": unit["unit_id"],
                "chapter_summary": "Synthetic clean chapter.",
                "records": [],
            }
            if stage == "B":
                payload["reviewed_pass_a_record_ids"] = []
            path = self.project.root / "work" / f"board-fixture-{stage}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            complete_book_map_unit(
                self.project, self.map_id, stage, unit["unit_id"],
                attempt_uid=unit["active_attempt_uid"], result=path,
            )
        self.map = finalize_book_map(self.project, self.map_id)
        paragraph = self.project.connection.execute(
            "SELECT stable_id,text FROM paragraphs WHERE revision_uid=? AND text LIKE ?",
            (self.revision, "%bronze key%"),
        ).fetchone()
        self.anchor = {
            "revision_uid": self.revision,
            "paragraph_stable_id": paragraph["stable_id"],
            "exact_quote": paragraph["text"],
            "start_offset": 0,
            "end_offset": len(paragraph["text"]),
        }

    def tearDown(self):
        self.project.close()
        self.tmp.cleanup()

    def submission(self):
        return {
            "schema_version": 1,
            "map_run_id": self.map_id,
            "revision_uid": self.revision,
            "source_sha256": self.map["source_sha256"],
            "reviews": [
                {
                    "persona_id": "reader_market", "summary": "Reader lens.",
                    "findings": [self.finding("r1", "hook", "concern", "major")],
                },
                {
                    "persona_id": "voice_emotion", "summary": "Voice lens.",
                    "findings": [self.finding("r2", "hook", "strength", "none")],
                },
                {
                    "persona_id": "structure_logic", "summary": "Logic lens.",
                    "findings": [self.finding("r3", "logic", "concern", "moderate")],
                },
            ],
        }

    def finding(self, fid, cause, kind, severity):
        return {
            "id": fid, "root_cause_id": cause, "kind": kind,
            "severity": severity, "statement": f"Synthetic {fid} observation.",
            "rationale": "Source-grounded synthetic example.",
            "recommendation": "Review and decide as author.",
            "evidence": [copy.deepcopy(self.anchor)],
        }

    def _finish_lens(self, review_id, persona_id, findings=None):
        claimed = claim_editorial_lens_unit(
            self.project, self.map_id, review_id, persona_id,
            worker_id="synthetic-agent", include_text=True,
        )
        unit = claimed["next_unit"]
        self.assertIsNotNone(unit)
        payload = {
            "schema_version": 1,
            "review_id": review_id,
            "map_run_id": self.map_id,
            "persona_id": persona_id,
            "revision_uid": self.revision,
            "source_sha256": self.map["source_sha256"],
            "chapter_stable_id": unit["unit_id"],
            "chapter_summary": "Synthetic chapter lens, explicitly reviewed.",
            "findings": findings or [],
        }
        path = self.project.root / "work" / f"{review_id}-{persona_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        completed = complete_editorial_lens_unit(
            self.project, self.map_id, review_id, persona_id, unit["unit_id"],
            attempt_uid=unit["active_attempt_uid"], result=path,
        )
        self.assertTrue(completed["validated"])
        self.assertTrue(completed["run"]["coverage_complete"])

    def test_resumable_editorial_lenses_reuse_er014_and_preview_board(self):
        review_id = "reader-board-01"
        started = start_editorial_lenses(self.project, self.map_id, review_id)
        self.assertEqual(len(started["runs"]), 3)
        self.assertFalse(started["ready_for_board_preview"])
        self.assertEqual(started, start_editorial_lenses(self.project, self.map_id, review_id))
        with self.assertRaisesRegex(ValueError, "intact whole-book coverage"):
            preview_completed_editorial_lenses(self.project, self.map_id, review_id)
        self._finish_lens(
            review_id, "reader_market",
            [self.finding("m-reader", "opening", "concern", "major")],
        )
        self._finish_lens(
            review_id, "voice_emotion",
            [self.finding("m-voice", "opening", "strength", "none")],
        )
        self._finish_lens(
            review_id, "structure_logic",
            [self.finding("m-logic", "cause", "concern", "moderate")],
        )
        board = preview_completed_editorial_lenses(self.project, self.map_id, review_id)
        self.assertTrue(board["ok"])
        self.assertTrue(board["lens_coverage_verified"])
        self.assertFalse(board["board_adjudication_complete"])
        self.assertEqual(board["editorial_board"]["unresolved_disagreements"], ["opening"])
        self.assertEqual(len(board["correction_list"]), 2)
        self.assertFalse(board["author_approval_recorded"])

    def test_invalid_editorial_unit_does_not_commit_checkpoint(self):
        review_id = "invalid-review"
        start_editorial_lenses(self.project, self.map_id, review_id)
        claimed = claim_editorial_lens_unit(
            self.project, self.map_id, review_id, "reader_market",
            worker_id="synthetic-agent", include_text=True,
        )
        unit = claimed["next_unit"]
        payload = {
            "schema_version": 1, "review_id": review_id,
            "map_run_id": self.map_id, "persona_id": "reader_market",
            "revision_uid": self.revision, "source_sha256": self.map["source_sha256"],
            "chapter_stable_id": unit["unit_id"], "chapter_summary": "bad evidence",
            "findings": [self.finding("invented", "issue", "concern", "major")],
        }
        payload["findings"][0]["evidence"][0]["exact_quote"] = "Invented passage."
        file = self.project.root / "work" / "bad-editorial.json"
        file.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid editorial evidence"):
            complete_editorial_lens_unit(
                self.project, self.map_id, review_id, "reader_market", unit["unit_id"],
                attempt_uid=unit["active_attempt_uid"], result=file,
            )
        status = editorial_lenses_status(self.project, self.map_id, review_id)
        self.assertEqual(status["runs"]["reader_market"]["counts"]["completed"], 0)

    def test_editorial_run_resume_after_provider_usage_limit(self):
        review_id = "paused-review"
        start_editorial_lenses(self.project, self.map_id, review_id)
        first = claim_editorial_lens_unit(
            self.project, self.map_id, review_id, "reader_market",
            worker_id="worker",
        )["next_unit"]
        run_id = f"editorial-{review_id}-reader_market"
        self.project.checkpoint_run(
            run_id, "blocked_by_usage_limit", reason="Synthetic usage limit",
        )
        resumed = self.project.resume_run(run_id)
        self.assertEqual(resumed["counts"]["pending"], 1)
        retry = claim_editorial_lens_unit(
            self.project, self.map_id, review_id, "reader_market",
            worker_id="worker-retry",
        )["next_unit"]
        self.assertEqual(first["unit_id"], retry["unit_id"])
        self.assertNotEqual(first["active_attempt_uid"], retry["active_attempt_uid"])

    def _ready_for_chair(self, review_id="board-synthesis-test"):
        start_editorial_lenses(self.project, self.map_id, review_id)
        self._finish_lens(
            review_id, "reader_market", [self.finding("reader-a", "opening", "concern", "major")]
        )
        self._finish_lens(
            review_id, "voice_emotion", [self.finding("voice-a", "opening", "strength", "none")]
        )
        self._finish_lens(
            review_id, "structure_logic", [self.finding("logic-a", "causality", "concern", "moderate")]
        )
        return review_id

    def _chair_decision(self, review_id):
        return {
            "schema_version": 1, "map_run_id": self.map_id, "review_id": review_id,
            "revision_uid": self.revision, "source_sha256": self.map["source_sha256"],
            "chair_persona_id": "editorial_board",
            "verdict": {
                "classification": "revision_recommended",
                "summary": "Some improvements are worth considering.",
                "rationale": "Synthetic evidence inspected across three distinct lenses.",
            },
            "groups": [
                {
                    "canonical_id": "opening",
                    "source_root_cause_ids": ["opening"],
                    "disposition": "revise", "category": "subjective",
                    "assessment": "Reader concerns outweigh this specific positive note.",
                    "rationale": "The source may benefit from a sharper opening.",
                    "recommendation": "Ask the author to clarify the opening premise.",
                    "dissent_resolution": "Voice editor's opposing positive assessment is preserved.",
                    "depends_on": ["causality"], "conflicts_with": [],
                },
                {
                    "canonical_id": "causality",
                    "source_root_cause_ids": ["causality"],
                    "disposition": "monitor", "category": "mixed",
                    "assessment": "Check whether the causal order is intentional.",
                    "rationale": "Synthetic test keeps uncertain sequence nonfinal.",
                    "recommendation": "", "dissent_resolution": "",
                    "depends_on": [], "conflicts_with": [],
                },
            ],
        }

    def test_chief_adjudication_preserves_dissent_and_prioritizes_dependencies(self):
        review = self._ready_for_chair()
        result = adjudicate_editorial_board(self.project, self.map_id, review, self._chair_decision(review))
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertTrue(result["board_adjudication_complete"])
        self.assertEqual(result["editorial_board"]["chair_name_en"], "Chief Sterling")
        self.assertFalse(result["editorial_board"]["independent_fourth_review"])
        self.assertEqual(
            [group["canonical_id"] for group in result["editorial_board"]["groups"]],
            ["causality", "opening"],
        )
        self.assertEqual(len(result["correction_list"]), 1)
        self.assertEqual(result["correction_list"][0]["correction_id"], "opening")
        self.assertEqual(result["correction_list"][0]["author_decision_status"], "pending")
        self.assertEqual(
            {item["id"] for editor in result["editors"] for item in editor["findings"]},
            {"reader-a", "voice-a", "logic-a"},
        )
        self.assertFalse(result["er013_safe_apply_invoked"])
        self.assertFalse(result["author_approval_recorded"])

    def test_no_findings_still_permits_explicated_no_action_verdict(self):
        review = "clean-board"
        start_editorial_lenses(self.project, self.map_id, review)
        for persona in ("reader_market", "voice_emotion", "structure_logic"):
            self._finish_lens(review, persona)
        decision = self._chair_decision(review)
        decision["groups"] = []
        decision["verdict"] = {
            "classification": "no_changes_recommended", "summary": "No actionable issues.",
            "rationale": "All three lenses reported zero issues on a clean synthetic chapter.",
        }
        result = adjudicate_editorial_board(self.project, self.map_id, review, decision)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(result["correction_list"], [])

    def test_board_rejects_missing_root_fake_revision_and_unexplained_dissent(self):
        review = self._ready_for_chair("invalid-synthesis")
        for variant in ("missing", "foreign", "dissent", "duplicates", "unknown-link", "cycle"):
            value = self._chair_decision(review)
            if variant == "missing":
                value["groups"].pop()
            elif variant == "foreign":
                value["source_sha256"] = "wrong SHA"
            elif variant == "dissent":
                value["groups"][0]["dissent_resolution"] = ""
            elif variant == "duplicates":
                value["groups"][1]["source_root_cause_ids"] = ["opening"]
            elif variant == "unknown-link":
                value["groups"][0]["depends_on"] = ["not-a-real-group"]
            elif variant == "cycle":
                value["groups"][1]["depends_on"] = ["opening"]
            with self.subTest(variant=variant):
                result = adjudicate_editorial_board(self.project, self.map_id, review, value)
                self.assertFalse(result["ok"], variant)

    def test_semantic_merge_needs_explicit_rationale_and_preserves_each_finding(self):
        review = self._ready_for_chair("explicit-merge")
        decision = self._chair_decision(review)
        decision["groups"] = [{
            "canonical_id": "combined",
            "source_root_cause_ids": ["opening", "causality"],
            "disposition": "needs_author_decision", "category": "mixed",
            "assessment": "Two explicit editorial topics are related.",
            "rationale": "Manual board attribution only.",
            "merge_rationale": "The author should decide if these concern the same structural decision.",
            "dissent_resolution": "The voice position remains visible as dissent.",
            "recommendation": "Ask the author about the combined structural intent.",
            "depends_on": [], "conflicts_with": [],
        }]
        result = adjudicate_editorial_board(self.project, self.map_id, review, decision)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["correction_list"]), 1)
        self.assertEqual(len(result["correction_list"][0]["finding_ids"]), 3)
        self.assertEqual(result["editorial_board"]["unresolved_author_decisions"], ["combined"])
        decision["groups"][0]["merge_rationale"] = ""
        self.assertFalse(adjudicate_editorial_board(self.project, self.map_id, review, decision)["ok"])

    def test_cli_board_adjudication_exits_2_on_invalid_decision(self):
        review = self._ready_for_chair("cli-synthesis")
        decision_path = self.root / "decisions.json"
        cmd = [
            sys.executable, str(ROOT / "scripts" / "editroast.py"),
            "editorial", "adjudicate", str(self.project.root), self.map_id, review,
            "--decisions", str(decision_path),
        ]
        decision_path.write_text(json.dumps(self._chair_decision(review)), encoding="utf-8")
        ok = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertTrue(json.loads(ok.stdout)["board_adjudication_complete"])
        bad = self._chair_decision(review)
        bad["groups"][0]["source_root_cause_ids"] = ["unknown"]
        decision_path.write_text(json.dumps(bad), encoding="utf-8")
        failed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)

    def test_three_lenses_merge_explicit_causes_without_forcing_consensus(self):
        result = preview_editorial_board(self.project, self.map_id, self.submission())
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual([x["persona_id"] for x in result["editors"]],
                         ["reader_market", "voice_emotion", "structure_logic"])
        self.assertEqual(result["editorial_board"]["chair_name_en"], "Chief Sterling")
        self.assertFalse(result["editorial_board"]["independent_fourth_review"])
        self.assertEqual(result["editorial_board"]["verdict"], "pending_board_synthesis")
        self.assertEqual(result["editorial_board"]["unresolved_disagreements"], ["hook"])
        self.assertEqual(result["editorial_board"]["root_causes"][0]["finding_ids"], ["r1", "r2"])
        self.assertEqual(len(result["correction_list"]), 2)
        self.assertTrue(all(item["author_decision_status"] == "pending"
                            and not item["applied"] for item in result["correction_list"]))
        self.assertFalse(result["changes_applied"])
        self.assertFalse(result["editorial_quality_assessed"])

    def test_zero_finding_lenses_are_valid(self):
        value = self.submission()
        for review in value["reviews"]:
            review["findings"] = []
        result = preview_editorial_board(self.project, self.map_id, value)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(result["editorial_board"]["root_causes"], [])
        self.assertEqual(result["correction_list"], [])

    def test_false_quote_revision_and_bad_id_are_rejected(self):
        for variant in ("quote", "revision", "duplicate_id", "bad_severity"):
            value = self.submission()
            finding = value["reviews"][0]["findings"][0]
            if variant == "quote":
                finding["evidence"][0]["exact_quote"] = "Invented quote."
            elif variant == "revision":
                finding["evidence"][0]["revision_uid"] = "not-this-revision"
            elif variant == "duplicate_id":
                value["reviews"][1]["findings"][0]["id"] = "r1"
            else:
                finding["severity"] = []
            with self.subTest(variant=variant):
                result = preview_editorial_board(self.project, self.map_id, value)
                self.assertFalse(result["ok"])

    def test_three_independent_lenses_and_finalized_map_required(self):
        value = self.submission()
        value["reviews"].pop()
        with self.assertRaisesRegex(ValueError, "Exactly three"):
            preview_editorial_board(self.project, self.map_id, value)
        value = self.submission()
        value["reviews"][1]["persona_id"] = "reader_market"
        self.assertFalse(preview_editorial_board(self.project, self.map_id, value)["ok"])
        with self.assertRaisesRegex(ValueError, "not finalized"):
            preview_editorial_board(self.project, "unknown-map", self.submission())

    def test_cli_exit_code_and_json_for_valid_and_invalid_reviews(self):
        path = self.root / "input.json"
        cmd = [
            sys.executable, str(ROOT / "scripts" / "editroast.py"),
            "editorial", "board-preview", str(self.project.root),
            self.map_id, "--reviews", str(path),
        ]
        path.write_text(json.dumps(self.submission()), encoding="utf-8")
        ok = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertTrue(json.loads(ok.stdout)["ok"])
        value = self.submission()
        value["reviews"][0]["findings"][0]["evidence"][0]["exact_quote"] = "Missing."
        path.write_text(json.dumps(value), encoding="utf-8")
        rejected = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
