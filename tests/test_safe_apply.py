from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.manuscript_state import ManuscriptProject, extract_manuscript  # noqa: E402


DOCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Alpha typo.</w:t></w:r></w:p>
    <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def make_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", DOCX_XML)
        archive.writestr("word/media/synthetic.bin", b"preserved")


def paragraph_id(project: ManuscriptProject, revision_uid: str, order: int) -> str:
    return project.connection.execute(
        "SELECT stable_id FROM paragraphs WHERE revision_uid=? AND source_order=?",
        (revision_uid, order),
    ).fetchone()[0]


class SafeApplyTests(unittest.TestCase):
    def test_approval_events_and_semantic_group_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Alice walks.\nBob waits.", encoding="utf-8")
            with ManuscriptProject.create(root / "project") as project:
                revision = project.import_manuscript(source)
                for index, (before, after) in enumerate((("walks", "runs"), ("waits", "leaves")), 1):
                    project.add_correction(
                        f"S{index}", revision["revision_uid"],
                        paragraph_stable_id=paragraph_id(project, revision["revision_uid"], index - 1),
                        exact_before=before, replacement=after, change_class="semantic",
                        group_uid="scene-rewrite",
                    )
                with self.assertRaisesRegex(ValueError, "author approval"):
                    project.decide_corrections(
                        "approved", group_uid="scene-rewrite", confirm_semantic_group=True
                    )
                with self.assertRaisesRegex(ValueError, "semantic-group"):
                    project.decide_corrections(
                        "approved", group_uid="scene-rewrite", author_confirmed=True
                    )
                approved = project.decide_corrections(
                    "approved", group_uid="scene-rewrite", author_confirmed=True,
                    confirm_semantic_group=True, actor="author-test",
                )
                self.assertEqual([item["status"] for item in approved], ["approved", "approved"])
                self.assertTrue(all(item["history"][-1]["batch_uid"] for item in approved))
                deferred = project.decide_corrections(
                    "deferred", correction_ids=["S1"], actor="author-test"
                )
                self.assertEqual(deferred[0]["status"], "deferred")
                rejected = project.decide_corrections(
                    "rejected", correction_ids=["S2"], actor="author-test"
                )
                self.assertEqual(rejected[0]["status"], "rejected")

    def test_preview_and_apply_text_create_verified_successor_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            original = b"Chapter One\nAlice is teh hero.\nKeep this line."
            source.write_bytes(original)
            with ManuscriptProject.create(root / "project") as project:
                first = project.import_manuscript(source)
                first_snapshot = Path(first["snapshot"])
                project.add_correction(
                    "M1", first["revision_uid"],
                    paragraph_stable_id=paragraph_id(project, first["revision_uid"], 1),
                    exact_before="teh", replacement="the", change_class="mechanical",
                )
                self.assertFalse(project.preview_correction_apply(first["revision_uid"])["ready"])
                project.decide_corrections(
                    "approved", correction_ids=["M1"], author_confirmed=True
                )
                revision_count = project.status()["revision_count"]
                preview = project.preview_correction_apply(first["revision_uid"])
                self.assertTrue(preview["ready"], preview["blockers"])
                self.assertEqual(project.status()["revision_count"], revision_count)
                self.assertEqual(preview["patches"][0]["before"], "teh")
                self.assertEqual(preview["patches"][0]["after"], "the")
                with self.assertRaisesRegex(ValueError, "plan hash changed"):
                    project.apply_corrections(
                        first["revision_uid"], plan_hash="0" * 64, author_confirmed=True
                    )
                with self.assertRaisesRegex(ValueError, "author apply confirmation"):
                    project.apply_corrections(
                        first["revision_uid"], plan_hash=preview["plan_hash"]
                    )
                applied = project.apply_corrections(
                    first["revision_uid"], plan_hash=preview["plan_hash"],
                    author_confirmed=True, actor="author-test",
                )
                self.assertEqual(applied["result_revision_uid"], "r000002")
                self.assertEqual(first_snapshot.read_bytes(), original)
                self.assertEqual(
                    Path(applied["result_snapshot"]).read_bytes(),
                    b"Chapter One\nAlice is the hero.\nKeep this line.",
                )
                self.assertEqual(project.correction("M1")["status"], "applied")
                diff = project.revision_diff("r000002", include_text=True)
                changed = [
                    group for group in diff["groups"]
                    if group["entity_type"] == "paragraph"
                    and group["effective_relation_type"] == "changed"
                ]
                self.assertEqual(len(changed), 1)
                self.assertTrue(project.verify()["ok"])

    def test_stale_tampered_ambiguous_and_overlapping_plans_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("abcdef", encoding="utf-8")
            with ManuscriptProject.create(root / "overlap") as project:
                revision = project.import_manuscript(source)
                pid = paragraph_id(project, revision["revision_uid"], 0)
                project.add_correction(
                    "O1", revision["revision_uid"], paragraph_stable_id=pid,
                    exact_before="bcd", replacement="B", change_class="mechanical",
                )
                project.add_correction(
                    "O2", revision["revision_uid"], paragraph_stable_id=pid,
                    exact_before="cde", replacement="C", change_class="mechanical",
                )
                project.decide_corrections(
                    "approved", correction_ids=["O1", "O2"], author_confirmed=True
                )
                preview = project.preview_correction_apply(revision["revision_uid"])
                self.assertFalse(preview["ready"])
                self.assertIn("overlapping_corrections", {item["code"] for item in preview["blockers"]})

            source.write_text("Repeat.\nRepeat.", encoding="utf-8")
            with ManuscriptProject.create(root / "ambiguous") as project:
                project.import_manuscript(source)
                source.write_text("Repeat.\nRepeat.\nRepeat.", encoding="utf-8")
                revision = project.import_manuscript(source)
                ambiguous = project.connection.execute(
                    """SELECT stable_id FROM paragraphs
                       WHERE revision_uid=? AND reconciliation_status='ambiguous' LIMIT 1""",
                    (revision["revision_uid"],),
                ).fetchone()[0]
                project.add_correction(
                    "A1", revision["revision_uid"], paragraph_stable_id=ambiguous,
                    exact_before="Repeat.", replacement="Repeated.", change_class="mechanical",
                )
                with self.assertRaisesRegex(ValueError, "ambiguous target"):
                    project.decide_corrections(
                        "approved", all_mechanical=True,
                        revision_uid=revision["revision_uid"], author_confirmed=True
                    )
                project.decide_corrections(
                    "approved", correction_ids=["A1"], author_confirmed=True
                )
                preview = project.preview_correction_apply(revision["revision_uid"])
                self.assertIn("ambiguous_reconciliation", {item["code"] for item in preview["blockers"]})

            source.write_text("Stable source", encoding="utf-8")
            with ManuscriptProject.create(root / "integrity") as project:
                first = project.import_manuscript(source)
                pid = paragraph_id(project, first["revision_uid"], 0)
                project.add_correction(
                    "I1", first["revision_uid"], paragraph_stable_id=pid,
                    exact_before="Stable", replacement="Verified", change_class="mechanical",
                )
                project.decide_corrections(
                    "approved", correction_ids=["I1"], author_confirmed=True
                )
                source.write_text("Newer source", encoding="utf-8")
                project.import_manuscript(source)
                stale = project.preview_correction_apply(first["revision_uid"], correction_ids=["I1"])
                self.assertIn("source_not_latest", {item["code"] for item in stale["blockers"]})

            source.write_text("Untampered", encoding="utf-8")
            with ManuscriptProject.create(root / "tampered") as project:
                revision = project.import_manuscript(source)
                pid = paragraph_id(project, revision["revision_uid"], 0)
                project.add_correction(
                    "T1", revision["revision_uid"], paragraph_stable_id=pid,
                    exact_before="Untampered", replacement="Verified", change_class="mechanical",
                )
                project.decide_corrections(
                    "approved", correction_ids=["T1"], author_confirmed=True
                )
                snapshot = Path(revision["snapshot"])
                snapshot.chmod(0o600)
                snapshot.write_text("tampered", encoding="utf-8")
                tampered = project.preview_correction_apply(revision["revision_uid"])
                self.assertIn("source_hash_mismatch", {item["code"] for item in tampered["blockers"]})

    def test_docx_apply_preserves_archive_and_blocks_cross_run_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.docx"
            make_docx(source)
            with ManuscriptProject.create(root / "project") as project:
                revision = project.import_manuscript(source)
                project.add_correction(
                    "D1", revision["revision_uid"],
                    paragraph_stable_id=paragraph_id(project, revision["revision_uid"], 0),
                    exact_before="typo", replacement="fixed", change_class="mechanical",
                )
                project.add_correction(
                    "D2", revision["revision_uid"],
                    paragraph_stable_id=paragraph_id(project, revision["revision_uid"], 1),
                    exact_before="Bold italic", replacement="Plain", change_class="semantic",
                )
                project.decide_corrections(
                    "approved", correction_ids=["D1"], author_confirmed=True
                )
                project.decide_corrections(
                    "approved", correction_ids=["D2"], author_confirmed=True
                )
                collision = project.preview_correction_apply(
                    revision["revision_uid"], correction_ids=["D2"]
                )
                self.assertFalse(collision["ready"])
                self.assertEqual(collision["blockers"][0]["code"], "formatting_collision")
                preview = project.preview_correction_apply(
                    revision["revision_uid"], correction_ids=["D1"]
                )
                self.assertTrue(preview["ready"], preview["blockers"])
                applied = project.apply_corrections(
                    revision["revision_uid"], correction_ids=["D1"],
                    plan_hash=preview["plan_hash"], author_confirmed=True,
                )
                rendered, _, _ = extract_manuscript(Path(applied["result_snapshot"]))
                self.assertEqual(rendered.paragraphs[0].text, "Alpha fixed.")
                self.assertEqual(rendered.paragraphs[1].text, "Bold italic")
                with zipfile.ZipFile(applied["result_snapshot"]) as archive:
                    self.assertEqual(archive.read("word/media/synthetic.bin"), b"preserved")

    def test_rollback_is_previewed_and_creates_another_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            original = b"# One\nA typo here."
            source.write_bytes(original)
            with ManuscriptProject.create(root / "project") as project:
                first = project.import_manuscript(source)
                project.add_correction(
                    "R1", first["revision_uid"],
                    paragraph_stable_id=paragraph_id(project, first["revision_uid"], 1),
                    exact_before="typo", replacement="fix", change_class="mechanical",
                )
                project.decide_corrections(
                    "approved", correction_ids=["R1"], author_confirmed=True
                )
                preview = project.preview_correction_apply(first["revision_uid"])
                applied = project.apply_corrections(
                    first["revision_uid"], plan_hash=preview["plan_hash"], author_confirmed=True
                )
                rollback_preview = project.preview_rollback(applied["application_uid"])
                self.assertTrue(rollback_preview["ready"])
                self.assertEqual(project.status()["revision_count"], 2)
                with self.assertRaisesRegex(ValueError, "rollback confirmation"):
                    project.rollback_application(
                        applied["application_uid"], plan_hash=rollback_preview["plan_hash"]
                    )
                rollback = project.rollback_application(
                    applied["application_uid"], plan_hash=rollback_preview["plan_hash"],
                    author_confirmed=True, actor="author-test",
                )
                self.assertEqual(rollback["result_revision_uid"], "r000003")
                self.assertEqual(Path(rollback["result_snapshot"]).read_bytes(), original)
                self.assertEqual(project.status()["revision_count"], 3)
                self.assertEqual(
                    [item["operation"] for item in project.application_history()],
                    ["apply", "rollback"],
                )
                self.assertTrue(project.verify()["ok"])

    def test_cli_preview_approval_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Typo teh.", encoding="utf-8")
            project_path = root / "project"
            wrapper = ROOT / "scripts" / "editroast.py"

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(wrapper), *arguments], cwd=ROOT,
                    capture_output=True, text=True, check=False,
                )

            self.assertEqual(run("project", "create", str(project_path)).returncode, 0)
            imported = run("manuscript", "import", str(project_path), str(source))
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            with ManuscriptProject.open(project_path) as project:
                pid = paragraph_id(project, "r000001", 0)
            added = run(
                "manuscript", "correction-add", str(project_path), "r000001", "CLI-1",
                "--paragraph", pid, "--before", "teh", "--after", "the",
                "--class", "mechanical",
            )
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            denied = run(
                "manuscript", "correction-decide", str(project_path), "accept",
                "--id", "CLI-1",
            )
            self.assertNotEqual(denied.returncode, 0)
            approved = run(
                "manuscript", "correction-decide", str(project_path), "accept",
                "--id", "CLI-1", "--confirm-author-approval",
            )
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            preview_result = run(
                "manuscript", "apply-preview", str(project_path), "r000001"
            )
            self.assertEqual(preview_result.returncode, 0, preview_result.stdout + preview_result.stderr)
            preview = json.loads(preview_result.stdout)
            self.assertTrue(preview["ready"])
            applied = run(
                "manuscript", "apply", str(project_path), "r000001",
                "--plan-hash", preview["plan_hash"], "--confirm-author-approval",
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            result = json.loads(applied.stdout)
            self.assertEqual(result["result_revision_uid"], "r000002")
            with ManuscriptProject.open(project_path) as project:
                self.assertEqual(
                    hashlib.sha256(Path(result["result_snapshot"]).read_bytes()).hexdigest(),
                    project.revision("r000002")["source_sha256"],
                )

    def test_schema_v4_migrates_existing_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Existing schema-three project", encoding="utf-8")
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                project.import_manuscript(source)
            database = project_path / "editroast.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    DROP TABLE correction_events;
                    DROP TABLE corrections;
                    DROP TABLE change_applications;
                    DELETE FROM schema_migrations WHERE version=4;
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with ManuscriptProject.open(project_path) as migrated:
                self.assertEqual(migrated.status()["schema_version"], 5)
                self.assertEqual(migrated.corrections(), [])
                self.assertEqual(migrated.application_history(), [])
                self.assertTrue(migrated.verify()["ok"])


if __name__ == "__main__":
    unittest.main()
