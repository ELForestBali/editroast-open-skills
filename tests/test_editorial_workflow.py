"""Synthetic ER-021 S4 tests; no author manuscript data is used."""
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
    advance_book_map,
    claim_book_map_unit,
    complete_book_map_unit,
    finalize_book_map,
    start_book_map,
)
from editroast_core.editorial_board import (
    claim_editorial_lens_unit,
    complete_editorial_lens_unit,
    start_editorial_lenses,
)
from editroast_core.editorial_workflow import (
    apply_board_corrections,
    author_board_report,
    board_status,
    claim_board_recheck_unit,
    complete_board_recheck_unit,
    decide_board_group,
    finalize_board_recheck,
    preview_board_apply,
    preview_board_rollback,
    record_board_decision,
    render_author_board_markdown,
    rollback_board_application,
    start_board_recheck,
)
from editroast_core.manuscript_state import ManuscriptProject
from editroast_core.project_bundle import export_project_bundle, import_project_bundle


class EditorialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "synthetic.md"
        self.source.write_text("# One\nMara returned the bronze key.\n", encoding="utf-8")
        self.project = ManuscriptProject.create(self.root / "project", language="en")
        self.revision = self.project.import_manuscript(self.source)["revision_uid"]
        self.map_id = "s4-map"
        self.review_id = "s4-review"
        start_book_map(self.project, self.revision, self.map_id)
        for pass_name in ("A", "B"):
            if pass_name == "B":
                advance_book_map(self.project, self.map_id)
            claimed = claim_book_map_unit(
                self.project, self.map_id, pass_name, worker_id="fixture", include_text=True
            )["next_unit"]
            payload = {
                "schema_version": 2,
                "map_run_id": self.map_id,
                "pass": pass_name,
                "revision_uid": self.revision,
                "chapter_stable_id": claimed["unit_id"],
                "chapter_summary": "Synthetic chapter.",
                "records": [],
            }
            if pass_name == "B":
                payload["reviewed_pass_a_record_ids"] = []
            path = self.project.root / "work" / f"map-{pass_name}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            complete_book_map_unit(
                self.project, self.map_id, pass_name, claimed["unit_id"],
                attempt_uid=claimed["active_attempt_uid"], result=path,
            )
        self.book_map = finalize_book_map(self.project, self.map_id)
        paragraph = self.project.connection.execute(
            "SELECT stable_id,text FROM paragraphs WHERE revision_uid=? AND text LIKE ?",
            (self.revision, "%bronze key%"),
        ).fetchone()
        self.paragraph_id = paragraph["stable_id"]
        self.anchor = {
            "revision_uid": self.revision,
            "paragraph_stable_id": self.paragraph_id,
            "exact_quote": paragraph["text"],
            "start_offset": 0,
            "end_offset": len(paragraph["text"]),
        }
        start_editorial_lenses(self.project, self.map_id, self.review_id)
        self._finish_lens("reader_market", [self._finding("reader", "opening", "concern", "major")])
        self._finish_lens("voice_emotion", [self._finding("voice", "opening", "strength", "none")])
        self._finish_lens("structure_logic", [self._finding("logic", "causality", "concern", "moderate")])

    def tearDown(self):
        self.project.close()
        self.tmp.cleanup()

    def _finding(self, finding_id, root, kind, severity):
        return {
            "id": finding_id,
            "root_cause_id": root,
            "kind": kind,
            "severity": severity,
            "statement": "Synthetic editorial observation.",
            "rationale": "The synthetic source is quoted exactly.",
            "recommendation": "Consider a precise author-approved revision.",
            "evidence": [copy.deepcopy(self.anchor)],
        }

    def _finish_lens(self, persona, findings):
        unit = claim_editorial_lens_unit(
            self.project, self.map_id, self.review_id, persona,
            worker_id="fixture", include_text=True,
        )["next_unit"]
        payload = {
            "schema_version": 1,
            "review_id": self.review_id,
            "map_run_id": self.map_id,
            "persona_id": persona,
            "revision_uid": self.revision,
            "source_sha256": self.book_map["source_sha256"],
            "chapter_stable_id": unit["unit_id"],
            "chapter_summary": "The synthetic chapter was reviewed.",
            "findings": findings,
        }
        path = self.project.root / "work" / f"lens-{persona}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        complete_editorial_lens_unit(
            self.project, self.map_id, self.review_id, persona, unit["unit_id"],
            attempt_uid=unit["active_attempt_uid"], result=path,
        )

    def _decision(self):
        return {
            "schema_version": 1,
            "map_run_id": self.map_id,
            "review_id": self.review_id,
            "revision_uid": self.revision,
            "source_sha256": self.book_map["source_sha256"],
            "chair_persona_id": "editorial_board",
            "verdict": {
                "classification": "revision_recommended",
                "summary": "A small revision is recommended.",
                "rationale": "All three source-grounded lenses were considered.",
            },
            "groups": [
                {
                    "canonical_id": "opening",
                    "source_root_cause_ids": ["opening"],
                    "disposition": "revise",
                    "category": "mixed",
                    "assessment": "The opening can be more precise.",
                    "rationale": "The dissent remains visible.",
                    "recommendation": "Replace bronze with brass if the author agrees.",
                    "dissent_resolution": "The positive voice assessment is preserved.",
                    "depends_on": ["causality"],
                    "conflicts_with": [],
                },
                {
                    "canonical_id": "causality",
                    "source_root_cause_ids": ["causality"],
                    "disposition": "monitor",
                    "category": "subjective",
                    "assessment": "The causal sequence is intentional in this fixture.",
                    "rationale": "No textual correction is necessary.",
                    "recommendation": "",
                    "dissent_resolution": "",
                    "depends_on": [],
                    "conflicts_with": [],
                },
            ],
        }

    def _changes(self):
        text = "Mara returned the bronze key."
        start = text.index("bronze")
        return {
            "schema_version": 1,
            "review_id": self.review_id,
            "revision_uid": self.revision,
            "canonical_id": "opening",
            "changes": [{
                "correction_id": "board-opening-01",
                "paragraph_stable_id": self.paragraph_id,
                "exact_before": "bronze",
                "replacement": "brass",
                "start_offset": start,
                "end_offset": start + len("bronze"),
                "change_class": "mechanical",
                "rationale": "Synthetic exact-span correction.",
            }],
        }

    def _record_and_approve(self):
        recorded = record_board_decision(
            self.project, self.map_id, self.review_id, self._decision()
        )
        self.assertTrue(recorded["ok"])
        decide_board_group(
            self.project, self.review_id, "causality", "approved",
            author_confirmed=True,
        )
        return decide_board_group(
            self.project, self.review_id, "opening", "approved",
            changes=self._changes(), author_confirmed=True,
        )

    def test_persisted_decision_is_idempotent_audited_and_integrity_checked(self):
        first = record_board_decision(
            self.project, self.map_id, self.review_id, self._decision(), actor="chief-agent"
        )
        second = record_board_decision(
            self.project, self.map_id, self.review_id, self._decision(), actor="chief-agent"
        )
        self.assertEqual(first["decision_sha256"], second["decision_sha256"])
        self.assertEqual(
            [g["author_decision_status"] for g in first["groups"]],
            ["pending", "pending"],
        )
        self.assertEqual(
            self.project.connection.execute(
                "SELECT COUNT(*) FROM editorial_board_events WHERE review_id=?",
                (self.review_id,),
            ).fetchone()[0],
            1,
        )
        self.assertTrue(self.project.verify()["ok"])
        with self.project.connection:
            self.project.connection.execute(
                "UPDATE editorial_board_reviews SET source_sha256='tampered' WHERE review_id=?",
                (self.review_id,),
            )
        self.assertFalse(self.project.verify()["ok"])

    def test_author_gate_dependencies_and_only_approval_creates_er013_corrections(self):
        record_board_decision(self.project, self.map_id, self.review_id, self._decision())
        decide_board_group(self.project, self.review_id, "opening", "deferred")
        self.assertEqual(self.project.corrections(self.revision), [])
        with self.assertRaisesRegex(ValueError, "dependency causality"):
            decide_board_group(
                self.project, self.review_id, "opening", "approved",
                changes=self._changes(), author_confirmed=True,
            )
        decide_board_group(
            self.project, self.review_id, "causality", "approved", author_confirmed=True
        )
        with self.assertRaisesRegex(ValueError, "confirmation"):
            decide_board_group(
                self.project, self.review_id, "opening", "approved", changes=self._changes()
            )
        result = decide_board_group(
            self.project, self.review_id, "opening", "approved",
            changes=self._changes(), author_confirmed=True,
        )
        opening = next(group for group in result["groups"] if group["canonical_id"] == "opening")
        self.assertEqual(opening["correction_ids"], ["board-opening-01"])
        self.assertEqual(opening["corrections"][0]["status"], "approved")

    def test_approved_conflicting_group_is_blocked_and_recheck_resumes(self):
        decision = self._decision()
        decision["groups"][0]["depends_on"] = []
        decision["groups"][0]["conflicts_with"] = ["causality"]
        record_board_decision(self.project, self.map_id, self.review_id, decision)
        decide_board_group(
            self.project, self.review_id, "causality", "approved", author_confirmed=True
        )
        with self.assertRaisesRegex(ValueError, "conflicts with approved"):
            decide_board_group(
                self.project, self.review_id, "opening", "approved",
                changes=self._changes(), author_confirmed=True,
            )

        # Use a fresh review to exercise the existing ER-014 usage-limit path
        # for the single comparative run.
        other = "s4-resume"
        decision = self._decision()
        decision["review_id"] = other
        start_editorial_lenses(self.project, self.map_id, other)
        original = self.review_id
        self.review_id = other
        self._finish_lens("reader_market", [self._finding("resume-reader", "opening", "concern", "major")])
        self._finish_lens("voice_emotion", [self._finding("resume-voice", "opening", "strength", "none")])
        self._finish_lens("structure_logic", [self._finding("resume-logic", "causality", "concern", "moderate")])
        record_board_decision(self.project, self.map_id, other, decision)
        decide_board_group(self.project, other, "causality", "approved", author_confirmed=True)
        changes = self._changes()
        changes["review_id"] = other
        changes["changes"][0]["correction_id"] = "board-resume-01"
        decide_board_group(
            self.project, other, "opening", "approved", changes=changes,
            author_confirmed=True,
        )
        preview = preview_board_apply(self.project, other)
        applied = apply_board_corrections(
            self.project, other, plan_hash=preview["plan_hash"], author_confirmed=True
        )
        start_board_recheck(
            self.project, other, "resume-recheck", applied["result_revision_uid"]
        )
        first = claim_board_recheck_unit(
            self.project, "resume-recheck", worker_id="first"
        )["next_unit"]
        run_id = "editorial-recheck-resume-recheck"
        self.project.checkpoint_run(
            run_id, "blocked_by_usage_limit", reason="Synthetic provider limit"
        )
        self.project.resume_run(run_id)
        retried = claim_board_recheck_unit(
            self.project, "resume-recheck", worker_id="second"
        )["next_unit"]
        self.assertEqual(first["unit_id"], retried["unit_id"])
        self.assertNotEqual(first["active_attempt_uid"], retried["active_attempt_uid"])
        self.review_id = original

    def test_safe_apply_rollback_and_single_resumable_board_recheck(self):
        self._record_and_approve()
        preview = preview_board_apply(self.project, self.review_id)
        self.assertTrue(preview["ready"], preview["blockers"])
        applied = apply_board_corrections(
            self.project, self.review_id, plan_hash=preview["plan_hash"],
            author_confirmed=True,
        )
        revised = applied["result_revision_uid"]
        self.assertEqual(self.project.revision(revised)["parent_revision_uid"], self.revision)
        start = start_board_recheck(self.project, self.review_id, "recheck-01", revised)
        self.assertTrue(start["single_comparative_report"])
        self.assertFalse(start["independent_lens_reruns"])
        unit = claim_board_recheck_unit(
            self.project, "recheck-01", worker_id="board-agent", include_text=True
        )["next_unit"]
        paragraph = self.project.connection.execute(
            "SELECT stable_id,text FROM paragraphs WHERE revision_uid=? AND text LIKE ?",
            (revised, "%brass key%"),
        ).fetchone()
        result = {
            "schema_version": 1,
            "recheck_id": "recheck-01",
            "review_id": self.review_id,
            "baseline_revision_uid": self.revision,
            "revision_uid": revised,
            "source_sha256": self.project.revision(revised)["source_sha256"],
            "chapter_stable_id": unit["unit_id"],
            "chapter_summary": "One comparative board pass reviewed the revised chapter.",
            "assessments": [{
                "id": "opening-improved",
                "classification": "improved",
                "source_group_ids": ["opening"],
                "statement": "The approved terminology is now present.",
                "rationale": "The revised source contains the exact approved replacement.",
                "evidence": [{
                    "revision_uid": revised,
                    "paragraph_stable_id": paragraph["stable_id"],
                    "exact_quote": "brass",
                    "start_offset": paragraph["text"].index("brass"),
                    "end_offset": paragraph["text"].index("brass") + len("brass"),
                }],
            }],
        }
        path = self.project.root / "work" / "recheck-result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        complete_board_recheck_unit(
            self.project, "recheck-01", unit["unit_id"],
            attempt_uid=unit["active_attempt_uid"], result=path,
        )
        report = finalize_board_recheck(self.project, "recheck-01", {
            "classification": "ready",
            "summary": "The approved issue improved and no new issue was found.",
            "rationale": "The single comparative pass has full verified coverage.",
        })
        self.assertEqual(len(report["improved"]), 1)
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(report["new"], [])
        rollback_preview = preview_board_rollback(
            self.project, self.review_id, applied["application_uid"]
        )
        rolled = rollback_board_application(
            self.project, self.review_id, applied["application_uid"],
            plan_hash=rollback_preview["plan_hash"], author_confirmed=True,
        )
        self.assertEqual(rolled["rollback_of_uid"], applied["application_uid"])

    def test_recheck_rejects_invented_quote_without_committing_checkpoint(self):
        self._record_and_approve()
        preview = preview_board_apply(self.project, self.review_id)
        applied = apply_board_corrections(
            self.project, self.review_id, plan_hash=preview["plan_hash"],
            author_confirmed=True,
        )
        revised = applied["result_revision_uid"]
        start_board_recheck(self.project, self.review_id, "bad-recheck", revised)
        unit = claim_board_recheck_unit(
            self.project, "bad-recheck", worker_id="agent", include_text=True
        )["next_unit"]
        payload = {
            "schema_version": 1, "recheck_id": "bad-recheck",
            "review_id": self.review_id, "baseline_revision_uid": self.revision,
            "revision_uid": revised,
            "source_sha256": self.project.revision(revised)["source_sha256"],
            "chapter_stable_id": unit["unit_id"], "chapter_summary": "Compared.",
            "assessments": [{
                "id": "invented", "classification": "new", "source_group_ids": [],
                "statement": "Invented issue.", "rationale": "Invalid evidence test.",
                "evidence": [{
                    "revision_uid": revised, "paragraph_stable_id": self.paragraph_id,
                    "exact_quote": "This text does not exist.",
                }],
            }],
        }
        path = self.project.root / "work" / "bad-recheck.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid Board Re-check evidence"):
            complete_board_recheck_unit(
                self.project, "bad-recheck", unit["unit_id"],
                attempt_uid=unit["active_attempt_uid"], result=path,
            )
        self.assertEqual(
            self.project.run_status("editorial-recheck-bad-recheck")["counts"]["completed"], 0
        )

    def test_author_json_markdown_and_portable_bundle_preserve_board_state(self):
        self._record_and_approve()
        report = author_board_report(self.project, self.review_id)
        self.assertEqual(
            list(report)[:6],
            ["schema_version", "review_id", "revision_uid", "verdict", "three_editors", "board_decision"],
        )
        self.assertEqual([item["name"] for item in report["three_editors"]],
                         ["Leo Shelf", "Vera Sharp", "Boris Marker"])
        markdown = render_author_board_markdown(report)
        self.assertLess(markdown.index("## Verdict"), markdown.index("## Three editors"))
        self.assertLess(markdown.index("## Three editors"), markdown.index("## Editorial Board decision"))
        bundle = self.root / "board.editroast"
        manifest = export_project_bundle(self.project, bundle)
        self.assertEqual(manifest["editorial_review_ids"], [self.review_id])
        restored_path = self.root / "restored"
        imported = import_project_bundle(bundle, restored_path)
        self.assertEqual(imported["editorial_review_ids"], [self.review_id])
        with ManuscriptProject.open(restored_path) as restored:
            restored_status = board_status(restored, self.review_id)
            self.assertEqual(restored_status["decision_sha256"], report_board_sha(self.project, self.review_id))
            self.assertTrue(restored.verify()["ok"])

    def test_cli_record_status_report_and_schema_v7(self):
        decision = self.root / "decision.json"
        decision.write_text(json.dumps(self._decision()), encoding="utf-8")
        base = [sys.executable, str(ROOT / "scripts" / "editroast.py"), "editorial"]
        recorded = subprocess.run(
            [*base, "record", str(self.project.root), self.map_id, self.review_id,
             "--decisions", str(decision)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertTrue(json.loads(recorded.stdout)["ok"])
        markdown = subprocess.run(
            [*base, "report", str(self.project.root), self.review_id, "--format", "markdown"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(markdown.returncode, 0, markdown.stdout + markdown.stderr)
        self.assertIn("# Editorial Board Report", markdown.stdout)
        self.assertEqual(self.project.status()["schema_version"], 7)

    def test_russian_utf8_exact_span_decision_uses_same_core(self):
        # The same persisted S4 engine accepts exact Unicode changes; there is no
        # language-specific fork or lossy transliteration.
        self._record_and_approve()
        russian_source = self.root / "ru.md"
        russian_source.write_text("# Глава первая\nМира вернула медный ключ.\n", encoding="utf-8")
        with ManuscriptProject.create(self.root / "ru-project", language="ru") as project:
            revision = project.import_manuscript(russian_source)["revision_uid"]
            row = project.connection.execute(
                "SELECT text FROM paragraphs WHERE revision_uid=? AND text LIKE ?",
                (revision, "%медный%"),
            ).fetchone()
            self.assertEqual(row["text"], "Мира вернула медный ключ.")
            self.assertEqual(project.status()["schema_version"], 7)
            self.assertTrue(project.verify()["ok"])


def report_board_sha(project: ManuscriptProject, review_id: str) -> str:
    return project.connection.execute(
        "SELECT decision_sha256 FROM editorial_board_reviews WHERE review_id=?", (review_id,)
    ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
