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
