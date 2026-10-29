"""Tests for source-bound ER-020 inventory and editorial evidence validation."""
from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.book_map import (
    advance_book_map,
    claim_book_map_unit,
    complete_book_map_unit,
    finalize_book_map,
    inventory_book,
    load_book_map,
    record_quality_assessment,
    start_book_map,
    validate_book_map,
)
from editroast_core.manuscript_state import ManuscriptProject
from editroast_core.project_bundle import export_project_bundle, import_project_bundle


class BookMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "synthetic.md"
        source.write_text(
            "# Chapter One\n"
            "Mara promised to return the bronze key.\n"
            "She dreamed the moon fell into the harbor.\n"
            "# Chapter Eighteen\n"
            "Mara returned the bronze key.\n",
            encoding="utf-8",
        )
        self.project = ManuscriptProject.create(self.root / "project", language="en")
        self.revision = self.project.import_manuscript(source)["revision_uid"]

    def tearDown(self):
        self.project.close()
        self.temp.cleanup()

    def test_inventory_privacy_and_coverage(self):
        compact = inventory_book(self.project, self.revision)
        expanded = inventory_book(self.project, self.revision, include_text=True)
        self.assertEqual(compact["source_paragraph_count"], 5)
        self.assertEqual(compact["source_chapter_count"], 2)
        self.assertNotIn("bronze key", json.dumps(compact))
        self.assertIn("bronze key", json.dumps(expanded))
        self.assertFalse(compact["editorial_analysis_complete"])
        self.assertTrue(validate_book_map(self.project, self.revision, compact)["ok"])

    def test_evidence_supports_promises_payoffs_and_dreams(self):
        book = inventory_book(self.project, self.revision, include_text=True)
        observations = (
            ("promise", "literal", "Mara promised to return the bronze key."),
            ("payoff", "literal", "Mara returned the bronze key."),
            ("event", "dream", "She dreamed the moon fell into the harbor."),
        )
        for i, (kind, frame, quote) in enumerate(observations):
            para = next(p for ch in book["chapters"] for p in ch["paragraphs"]
                        if quote in p["text"])
            book["records"].append({
                "id": f"record-{i}", "kind": kind, "epistemic_status": "observed",
                "scope": "cross_book", "narrative_frame": frame,
                "statement": "A source-grounded observation.",
                "evidence": [{"revision_uid": self.revision,
                              "paragraph_stable_id": para["stable_id"],
                              "exact_quote": quote}],
            })
        result = validate_book_map(self.project, self.revision, book)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["evidence_checked"], 3)
        self.assertFalse(result["editorial_quality_assessed"])

    def test_missing_quote_and_foreign_revision_are_rejected(self):
        book = inventory_book(self.project, self.revision, include_text=True)
        para = book["chapters"][0]["paragraphs"][1]
        book["records"] = [{
            "id": "x", "kind": "event", "epistemic_status": "observed",
            "scope": "chapter", "narrative_frame": "literal",
            "statement": "Unverified assertion.",
            "evidence": [{"revision_uid": "unknown", "paragraph_stable_id": para["stable_id"],
                          "exact_quote": "This quotation does not exist."}],
        }]
        result = validate_book_map(self.project, self.revision, book)
        self.assertFalse(result["ok"])
        self.assertTrue(any("revision mismatch" in error for error in result["errors"]))
        self.assertTrue(any("quoted text does not match" in error for error in result["errors"]))

    def test_partial_inventory_or_malformed_field_rejected(self):
        book = inventory_book(self.project, self.revision)
        book["chapters"].pop()
        self.assertFalse(validate_book_map(self.project, self.revision, book)["ok"])
        book = inventory_book(self.project, self.revision)
        book["records"] = [{"id": "1", "kind": [], "evidence": []}]
        self.assertFalse(validate_book_map(self.project, self.revision, book)["ok"])

    def test_untrusted_json_enum_types_fail_validation_not_python_exception(self):
        from editroast_core.book_map import _validate_records

        inventory = inventory_book(self.project, self.revision, include_text=True)
        chapter = inventory["chapters"][0]
        paragraph = chapter["paragraphs"][1]
        source = paragraph["text"]
        good = {
            "id": "safe", "kind": "event", "record_class": "observed_fact",
            "epistemic_status": "observed", "scope": "chapter",
            "narrative_frame": "literal", "statement": "Synthetic observation",
            "review_status": "confirmed", "links": [],
            "pov_type": "close_third",
            "sequence": {"reading_order": 1, "chronology": "unknown", "story_time": None},
            "evidence": [{
                "revision_uid": self.revision,
                "paragraph_stable_id": paragraph["stable_id"],
                "exact_quote": source,
            }],
        }
        for field in ("record_class", "epistemic_status", "review_status", "pov_type"):
            with self.subTest(field=field):
                malformed = json.loads(json.dumps(good))
                malformed[field] = ["not", "a", "string"]
                errors, _, _ = _validate_records(
                    self.project, self.revision, [malformed],
                    strict=True, expected_chapter_id=chapter["stable_id"], expected_pass="B",
                )
                self.assertTrue(errors, f"{field} must be rejected without an exception")
        malformed = json.loads(json.dumps(good))
        malformed["sequence"]["chronology"] = {"unexpected": "object"}
        errors, _, _ = _validate_records(
            self.project, self.revision, [malformed],
            strict=True, expected_chapter_id=chapter["stable_id"], expected_pass="B",
        )
        self.assertTrue(errors, "non-string chronology must be rejected without an exception")

    def test_snapshot_tamper_is_rejected(self):
        book = inventory_book(self.project, self.revision)
        snapshot = Path(self.project.revision(self.revision)["snapshot"])
        # Immutable snapshots are intentionally read-only; simulate external tampering.
        snapshot.chmod(snapshot.stat().st_mode | stat.S_IWUSR)
        snapshot.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "integrity"):
            validate_book_map(self.project, self.revision, book)


class TwoPassBookMapTests(unittest.TestCase):
    def _record(
        self,
        record_id,
        kind,
        paragraph,
        revision,
        *,
        pass_name,
        frame="literal",
        links=None,
        record_class="observed_fact",
        epistemic="observed",
        sequence=False,
        pov_type=None,
    ):
        record = {
            "id": record_id,
            "kind": kind,
            "record_class": record_class,
            "epistemic_status": epistemic,
            "scope": "cross_book" if links else "chapter",
            "narrative_frame": frame,
            "statement": f"Synthetic {kind} observation {record_id}.",
            "review_status": "provisional" if pass_name == "A" else "confirmed",
            "links": links or [],
            "evidence": [{
                "revision_uid": revision,
                "paragraph_stable_id": paragraph["stable_id"],
                "exact_quote": paragraph["text"],
                "start_offset": 0,
                "end_offset": len(paragraph["text"]),
            }],
        }
        if sequence:
            record["sequence"] = {
                "reading_order": paragraph["source_order"],
                "story_time": "relative synthetic story time",
                "chronology": "nonlinear" if frame == "flashback" else "relative",
            }
        if pov_type:
            record["pov_type"] = pov_type
        return record

    def _write_result(self, project, map_id, pass_name, unit, payload):
        target = project.root / "work" / "book-maps" / map_id / pass_name
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{unit['unit_id']}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    def _pass_b_records(self, index, body, revision, locale):
        r = lambda *args, **kwargs: self._record(
            *args, revision=revision, pass_name="B", **kwargs
        )
        if index == 0:
            return [
                r("char-protagonist", "character", body[0]),
                r("char-ivo", "character", body[0]),
                r("relationship-main", "relationship", body[0], links=[
                    {"relation": "participant", "target_id": "char-protagonist"},
                    {"relation": "participant", "target_id": "char-ivo"},
                ]),
                r("alias-protagonist", "alias", body[0], links=[
                    {"relation": "alias_of", "target_id": "char-protagonist"}
                ]),
                r("plotline-key", "plotline", body[0]),
                r("goal-return-key", "goal", body[0], links=[
                    {"relation": "owner", "target_id": "char-protagonist"}
                ]),
                r("promise-key", "promise", body[0]),
                r("motif-boat", "motif", body[0]),
                r("symbol-boat", "symbol", body[0],
                  record_class="editorial_hypothesis", epistemic="inferred", links=[
                    {"relation": "interprets", "target_id": "motif-boat"}
                ]),
                r("genre-promise", "genre_expectation", body[0],
                  record_class="editorial_hypothesis", epistemic="inferred"),
                r("pov-unreliable-opening", "narrator_pov", body[1],
                  frame="unreliable_narration", pov_type="first_person"),
            ]
        if index == 1:
            return [
                r("dream-event", "event", body[0], frame="dream", sequence=True),
                r("dream-intent-decision", "authorial_device", body[0], frame="dream",
                  record_class="author_decision", epistemic="author_confirmed"),
                r("char-cameo", "character", body[1]),
                r("cameo-device", "authorial_device", body[1], links=[
                    {"relation": "uses", "target_id": "char-cameo"}
                ]),
            ]
        if index == 2:
            return [
                r("flashback-scene", "scene", body[0], frame="flashback", sequence=True),
                r("key-use-event", "event", body[0], frame="flashback", sequence=True),
                r("archive-fire-event", "event", body[0], frame="flashback", sequence=True),
                r("fire-cause", "causal_link", body[0], links=[
                    {"relation": "cause", "target_id": "key-use-event"},
                    {"relation": "effect", "target_id": "archive-fire-event"},
                ]),
                r("char-other-alias", "character", body[1], frame="ambiguous"),
                r("alias-collision", "alias", body[1], frame="ambiguous", links=[
                    {"relation": "possible_alias_of", "target_id": "char-other-alias"}
                ], record_class="editorial_hypothesis", epistemic="uncertain"),
                r("alias-question", "open_question", body[1], frame="ambiguous",
                  record_class="editorial_hypothesis", epistemic="uncertain"),
                r("alias-disagreement", "disagreement", body[1], frame="ambiguous",
                  record_class="editorial_hypothesis", epistemic="uncertain", links=[
                    {"relation": "concerns", "target_id": "alias-collision"}
                ]),
            ]
        if index == 3:
            return [
                r("payoff-key", "payoff", body[0], links=[
                    {"relation": "fulfills", "target_id": "promise-key"}
                ]),
                r("character-change", "change", body[0], links=[
                    {"relation": "changes", "target_id": "char-protagonist"}
                ]),
                r("cyclical-device", "authorial_device", body[0], links=[
                    {"relation": "repeats", "target_id": "motif-boat"}
                ]),
                r("pov-correction", "narrator_pov", body[1],
                  frame="unreliable_narration", pov_type="first_person", links=[
                    {"relation": "revises", "target_id": "pov-unreliable-opening"}
                ]),
            ]
        self.assertEqual(index, 4)
        return []

    def _run_fixture(self, fixture_name, locale, *, assess=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = ROOT / "tests" / "fixtures" / fixture_name
        project = ManuscriptProject.create(root / "project", language=locale)
        self.addCleanup(project.close)
        revision = project.import_manuscript(source)["revision_uid"]
        map_id = f"golden-{locale}"
        started = start_book_map(project, revision, map_id)
        self.assertEqual(started["pass_a"]["plan"]["scope"], "chapter")

        pass_a_ids = []
        index = 0
        while True:
            claimed = claim_book_map_unit(
                project, map_id, "A", worker_id="golden-a", include_text=True
            )
            unit = claimed["next_unit"]
            if unit is None:
                break
            chapter = claimed["task"]["chapter"]
            body = [
                paragraph for paragraph in chapter["paragraphs"]
                if paragraph["text"].strip() and paragraph["text"] != chapter["title"]
            ]
            records = []
            if index < 4:
                record_id = f"a-structure-{index}"
                pass_a_ids.append(record_id)
                records = [self._record(
                    record_id,
                    "narrative_structure",
                    body[0],
                    revision,
                    pass_name="A",
                )]
            result = {
                "schema_version": 2,
                "map_run_id": map_id,
                "pass": "A",
                "revision_uid": revision,
                "chapter_stable_id": unit["unit_id"],
                "chapter_summary": f"Pass A inventory chapter {index}.",
                "records": records,
            }
            result_path = self._write_result(project, map_id, "A", unit, result)
            complete_book_map_unit(
                project,
                map_id,
                "A",
                unit["unit_id"],
                attempt_uid=unit["active_attempt_uid"],
                result=result_path,
            )
            index += 1
        self.assertEqual(index, 5)
        advanced = advance_book_map(project, map_id)
        self.assertTrue(advanced["ready_for_pass_b"])

        index = 0
        while True:
            claimed = claim_book_map_unit(
                project, map_id, "B", worker_id="golden-b", include_text=True
            )
            unit = claimed["next_unit"]
            if unit is None:
                break
            chapter = claimed["task"]["chapter"]
            body = [
                paragraph for paragraph in chapter["paragraphs"]
                if paragraph["text"].strip() and paragraph["text"] != chapter["title"]
            ]
            result = {
                "schema_version": 2,
                "map_run_id": map_id,
                "pass": "B",
                "revision_uid": revision,
                "chapter_stable_id": unit["unit_id"],
                "chapter_summary": f"Pass B synthesis chapter {index}.",
                "records": self._pass_b_records(index, body, revision, locale),
                "reviewed_pass_a_record_ids": pass_a_ids if index == 0 else [],
            }
            result_path = self._write_result(project, map_id, "B", unit, result)
            complete_book_map_unit(
                project,
                map_id,
                "B",
                unit["unit_id"],
                attempt_uid=unit["active_attempt_uid"],
                result=result_path,
            )
            index += 1
        final = finalize_book_map(project, map_id)
        self.assertTrue(final["editorial_analysis_complete"])
        self.assertEqual(final["coverage"]["chapter_count"], 5)
        self.assertEqual(final["coverage"]["pass_a_records_reviewed"], 4)
        self.assertEqual(final["integrity_validation"]["status"], "validated")
        self.assertEqual(final["editorial_quality"]["status"], "not_assessed")
        self.assertEqual(
            next(record for record in final["records"] if record["id"] == "dream-event")
            ["narrative_frame"],
            "dream",
        )
        self.assertEqual(
            next(record for record in final["records"] if record["id"] == "flashback-scene")
            ["sequence"]["chronology"],
            "nonlinear",
        )
        payoff = next(record for record in final["records"] if record["id"] == "payoff-key")
        self.assertEqual(payoff["links"][0]["target_id"], "promise-key")
        self.assertTrue(final["unresolved_questions"])
        self.assertEqual(final["author_decisions"][0]["id"], "dream-intent-decision")
        self.assertEqual(
            [record for record in final["records"] if record["source_unit_id"] == final["chapters"][-1]["stable_id"]],
            [],
        )
        self.assertTrue(project.verify()["ok"])
        self.assertTrue(validate_book_map(project, revision, final)["ok"])

        if assess:
            assessed = record_quality_assessment(project, map_id, {
                "assessment_type": "synthetic_regression",
                "sample_size": 1,
                "locales": [locale],
                "metrics": {
                    "citation_validity": 1.0,
                    "important_device_recognition": 1.0,
                    "false_positive_traps_passed": 1.0,
                    "delayed_payoff_linked": 1.0,
                },
                "limitations": [
                    "Hand-authored golden map checks the contract, not model literary quality."
                ],
            })
            self.assertEqual(assessed["editorial_quality"]["status"], "assessed")

        bundle = root / f"{locale}.editroast"
        export_project_bundle(project, bundle)
        restored = root / "restored"
        import_project_bundle(bundle, restored)
        with ManuscriptProject.open(restored) as imported:
            restored_map = load_book_map(imported, map_id)
            self.assertEqual(restored_map["coverage"], final["coverage"])
            self.assertTrue(imported.verify()["ok"])
        return final

    def test_english_two_pass_golden_map_and_portable_round_trip(self):
        final = self._run_fixture("book-map-en.md", "en", assess=True)
        self.assertIn("cameo-device", {record["id"] for record in final["records"]})

    def test_russian_two_pass_golden_map_and_portable_round_trip(self):
        final = self._run_fixture("book-map-ru.md", "ru")
        self.assertIn("alias-collision", {record["id"] for record in final["records"]})

    def test_invalid_unit_result_never_becomes_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            source.write_text("# One\nA true sentence.\n", encoding="utf-8")
            with ManuscriptProject.create(root / "project") as project:
                revision = project.import_manuscript(source)["revision_uid"]
                start_book_map(project, revision, "invalid-result")
                claimed = claim_book_map_unit(
                    project, "invalid-result", "A", worker_id="worker", include_text=True
                )
                unit = claimed["next_unit"]
                bad = {
                    "schema_version": 2,
                    "map_run_id": "invalid-result",
                    "pass": "A",
                    "revision_uid": revision,
                    "chapter_stable_id": unit["unit_id"],
                    "chapter_summary": "Bad quote must fail.",
                    "records": [{
                        "id": "bad", "kind": "event", "record_class": "observed_fact",
                        "epistemic_status": "observed", "scope": "chapter",
                        "narrative_frame": "literal", "statement": "Invented.",
                        "review_status": "provisional", "links": [],
                        "sequence": {"reading_order": 1, "story_time": None, "chronology": "unknown"},
                        "evidence": [{"revision_uid": revision,
                                      "paragraph_stable_id": unit["source_stable_id"],
                                      "exact_quote": "invented quote"}],
                    }],
                }
                result = self._write_result(project, "invalid-result", "A", unit, bad)
                with self.assertRaisesRegex(ValueError, "Invalid book-map unit result"):
                    complete_book_map_unit(
                        project, "invalid-result", "A", unit["unit_id"],
                        attempt_uid=unit["active_attempt_uid"], result=result,
                    )
                status = project.run_status("invalid-result.pass-a")
                self.assertEqual(status["counts"]["completed"], 0)
                self.assertEqual(status["counts"]["in_progress"], 1)

    def test_book_map_resume_reuses_er014_checkpoint_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            source.write_text("# One\nFirst.\n# Two\nSecond.\n", encoding="utf-8")
            with ManuscriptProject.create(root / "project") as project:
                revision = project.import_manuscript(source)["revision_uid"]
                start_book_map(project, revision, "resume-map")
                first = claim_book_map_unit(
                    project, "resume-map", "A", worker_id="worker", include_text=True
                )["next_unit"]
                first_result = self._write_result(project, "resume-map", "A", first, {
                    "schema_version": 2,
                    "map_run_id": "resume-map",
                    "pass": "A",
                    "revision_uid": revision,
                    "chapter_stable_id": first["unit_id"],
                    "chapter_summary": "First chapter inventoried.",
                    "records": [],
                })
                complete_book_map_unit(
                    project, "resume-map", "A", first["unit_id"],
                    attempt_uid=first["active_attempt_uid"], result=first_result,
                )
                interrupted = claim_book_map_unit(
                    project, "resume-map", "A", worker_id="worker", include_text=True
                )["next_unit"]
                project.checkpoint_run(
                    "resume-map.pass-a", "blocked_by_usage_limit", reason="synthetic limit"
                )
                resumed = project.resume_run("resume-map.pass-a")
                self.assertEqual(resumed["verified_completed"], 1)
                retried = claim_book_map_unit(
                    project, "resume-map", "A", worker_id="worker-2", include_text=True
                )["next_unit"]
                self.assertEqual(retried["unit_id"], interrupted["unit_id"])
                self.assertNotEqual(retried["active_attempt_uid"], interrupted["active_attempt_uid"])
                second_result = self._write_result(project, "resume-map", "A", retried, {
                    "schema_version": 2,
                    "map_run_id": "resume-map",
                    "pass": "A",
                    "revision_uid": revision,
                    "chapter_stable_id": retried["unit_id"],
                    "chapter_summary": "Second chapter inventoried after resume.",
                    "records": [],
                })
                complete_book_map_unit(
                    project, "resume-map", "A", retried["unit_id"],
                    attempt_uid=retried["active_attempt_uid"], result=second_result,
                )
                self.assertTrue(advance_book_map(project, "resume-map")["ready_for_pass_b"])

    def test_schema_v6_migrates_schema_v5_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            project_path = Path(temporary) / "project"
            with ManuscriptProject.create(project_path):
                pass
            connection = sqlite3.connect(project_path / "editroast.sqlite")
            try:
                connection.executescript(
                    """
                    DROP TABLE book_map_evidence;
                    DROP TABLE book_map_records;
                    DROP TABLE book_maps;
                    DELETE FROM schema_migrations WHERE version=6;
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with ManuscriptProject.open(project_path) as migrated:
                self.assertEqual(migrated.status()["schema_version"], 7)
                self.assertEqual(migrated.status()["book_map_count"], 0)
                self.assertTrue(migrated.verify()["ok"])

    def test_cli_starts_and_claims_two_pass_workflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            source.write_text("# One\nA source sentence.\n", encoding="utf-8")
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)["revision_uid"]
            wrapper = ROOT / "scripts" / "editroast.py"

            def run(*arguments):
                return subprocess.run(
                    [sys.executable, str(wrapper), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            started = run(
                "book-map", "start", str(project_path), "cli-map", revision
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            claimed = run(
                "book-map", "claim", str(project_path), "cli-map",
                "--pass", "A", "--worker", "cli", "--include-text",
            )
            self.assertEqual(claimed.returncode, 0, claimed.stdout + claimed.stderr)
            payload = json.loads(claimed.stdout)
            self.assertEqual(payload["task"]["pass"], "A")
            self.assertTrue(payload["task"]["manuscript_text_included"])


if __name__ == "__main__":
    unittest.main()
