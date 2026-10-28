from __future__ import annotations

import hashlib
import json
import os
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


def make_docx(path: Path, *, unsafe_name: str | None = None) -> None:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapitre Un</w:t></w:r></w:p>
    <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Élan </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>— déjà vu</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>Repeated paragraph.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Repeated paragraph.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
</w:styles>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/media/image1.png", b"synthetic-image")
        if unsafe_name:
            archive.writestr(unsafe_name, b"unsafe")


class ManuscriptStateTests(unittest.TestCase):
    def test_import_docx_txt_and_markdown_preserves_complete_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / "sources"
            sources.mkdir()
            docx = sources / "synthetic.docx"
            make_docx(docx)
            txt = sources / "synthetic.txt"
            txt.write_text("Chapter One\nCafé №1\n\nFinal line", encoding="utf-8")
            markdown = sources / "synthetic.md"
            markdown.write_text("# Título\n\n¿Dónde está?\n", encoding="utf-8")

            for source, expected_count in ((docx, 5), (txt, 4), (markdown, 3)):
                project_path = root / f"project-{source.suffix[1:]}"
                with ManuscriptProject.create(project_path, language="fr-FR") as project:
                    revision = project.import_manuscript(source)
                    self.assertEqual(revision["paragraph_count"], expected_count)
                    self.assertEqual(revision["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                    stored = project.connection.execute(
                        "SELECT COUNT(*),COUNT(DISTINCT source_order) FROM paragraphs WHERE revision_uid=?",
                        (revision["revision_uid"],),
                    ).fetchone()
                    self.assertEqual(tuple(stored), (expected_count, expected_count))
                    self.assertTrue(project.verify()["ok"])
            extracted, _, _ = extract_manuscript(docx)
            self.assertEqual(extracted.paragraphs[2].text, "")
            self.assertEqual(len(extracted.paragraphs[1].runs), 2)
            self.assertTrue(extracted.paragraphs[1].runs[0]["bold"])
            self.assertTrue(extracted.paragraphs[1].runs[1]["italic"])
            self.assertTrue(any("media" in warning for warning in extracted.warnings))

    def test_revision_graph_diff_and_repeated_anchor_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text(
                "Chapter One\nAlpha opening.\nRepeated paragraph.\nRepeated paragraph.\nRemoved ending.",
                encoding="utf-8",
            )
            with ManuscriptProject.create(root / "project", title="Synthetic") as project:
                first = project.import_manuscript(source)
                repeated_first = [
                    row[0] for row in project.connection.execute(
                        "SELECT stable_id FROM paragraphs WHERE revision_uid=? AND text=? ORDER BY source_order",
                        (first["revision_uid"], "Repeated paragraph."),
                    )
                ]
                self.assertEqual(len(repeated_first), 2)
                self.assertEqual(len(set(repeated_first)), 2)
                source.write_text(
                    "Chapter One\nAlpha opening changed.\nRepeated paragraph.\nRepeated paragraph.\nΩ wholly novel coda.",
                    encoding="utf-8",
                )
                second = project.import_manuscript(source)
                self.assertEqual(second["parent_revision_uid"], first["revision_uid"])
                self.assertGreaterEqual(second["changes"].get("changed", 0), 1)
                self.assertGreaterEqual(second["changes"].get("added", 0), 1)
                self.assertGreaterEqual(second["changes"].get("removed", 0), 1)
                repeated_second = [
                    row[0] for row in project.connection.execute(
                        "SELECT stable_id FROM paragraphs WHERE revision_uid=? AND text=? ORDER BY source_order",
                        (second["revision_uid"], "Repeated paragraph."),
                    )
                ]
                self.assertEqual(repeated_second, repeated_first)
                self.assertEqual(len(project.revisions()), 2)
                self.assertEqual(project.revision(first["revision_uid"])["source_name"], "book.txt")

    def test_ambiguous_repeat_is_marked_for_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Repeat.\nRepeat.", encoding="utf-8")
            with ManuscriptProject.create(root / "project") as project:
                project.import_manuscript(source)
                source.write_text("Repeat.\nRepeat.\nRepeat.", encoding="utf-8")
                second = project.import_manuscript(source)
                self.assertGreaterEqual(second["changes"].get("ambiguous", 0), 1)
                statuses = [row[0] for row in project.connection.execute(
                    "SELECT reconciliation_status FROM paragraphs WHERE revision_uid=?",
                    (second["revision_uid"],),
                )]
                self.assertIn("ambiguous", statuses)

    def test_decisions_findings_and_event_history_survive_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            source.write_text("# One\nText", encoding="utf-8")
            with ManuscriptProject.create(root / "project") as project:
                first = project.import_manuscript(source)
                paragraph_id = project.connection.execute(
                    "SELECT stable_id FROM paragraphs WHERE revision_uid=? ORDER BY source_order DESC LIMIT 1",
                    (first["revision_uid"],),
                ).fetchone()[0]
                project.record_decision("D-1", "editorial", revision_uid=first["revision_uid"])
                project.decide("D-1", "accepted", selected={"choice": "keep"}, rationale="Author choice")
                project.record_decision("D-2", "editorial", revision_uid=first["revision_uid"])
                project.decide("D-2", "rejected")
                project.record_decision("D-3", "editorial", revision_uid=first["revision_uid"])
                project.record_finding(
                    "F-1", first["revision_uid"], quote="Text", recommendation="Synthetic note",
                    paragraph_stable_id=paragraph_id,
                )
                source.write_text("# One\nText revised", encoding="utf-8")
                project.import_manuscript(source)
                self.assertEqual(project.status()["decision_counts"], {"accepted": 1, "pending": 1, "rejected": 1})
                self.assertEqual(project.status()["finding_count"], 1)
                self.assertEqual(
                    project.connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0], 5
                )

    def test_corrupt_and_unsafe_docx_do_not_damage_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "valid.txt"
            valid.write_text("Stable revision", encoding="utf-8")
            corrupt = root / "corrupt.docx"
            corrupt.write_bytes(b"not-a-zip")
            unsafe = root / "unsafe.docx"
            make_docx(unsafe, unsafe_name="../escape.txt")
            with ManuscriptProject.create(root / "project") as project:
                project.import_manuscript(valid)
                for bad in (corrupt, unsafe):
                    with self.assertRaises(ValueError):
                        project.import_manuscript(bad)
                self.assertEqual(project.status()["revision_count"], 1)
                self.assertTrue(project.verify()["ok"])

    def test_reopen_recovers_interrupted_staging_and_orphan_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with ManuscriptProject.create(project_path):
                pass
            (project_path / ".staging" / "partial.tmp").write_bytes(b"partial")
            orphan = project_path / "revisions" / "r999999"
            orphan.mkdir()
            (orphan / "source.txt").write_bytes(b"orphan")
            with ManuscriptProject.open(project_path) as reopened:
                self.assertFalse((project_path / ".staging" / "partial.tmp").exists())
                self.assertFalse(orphan.exists())
                self.assertEqual(reopened.status()["revision_count"], 0)

    def test_preview_ledger_migrates_without_breaking_legacy_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.txt"
            source.write_text("Legacy synthetic revision", encoding="utf-8")
            project_path = root / "project"
            legacy = ROOT / "scripts/editroast_state.py"

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(legacy), "--project", str(project_path), *args],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )

            self.assertEqual(run("import", str(source)).returncode, 0)
            self.assertEqual(run(
                "decision", "--id", "T-1", "--kind", "name", "--source", "X",
                "--alternatives", '["A","B"]',
            ).returncode, 0)
            with ManuscriptProject.open(project_path) as migrated:
                self.assertEqual(migrated.status()["revision_count"], 1)
                self.assertEqual(migrated.status()["decision_counts"], {"pending": 1})
                self.assertEqual(migrated.revisions()[0]["legacy_revision_id"], 1)
            legacy_status = run("status")
            self.assertEqual(legacy_status.returncode, 0, legacy_status.stdout + legacy_status.stderr)
            self.assertEqual(len(json.loads(legacy_status.stdout)["revisions"]), 1)

    def test_verify_detects_modified_and_missing_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Immutable source", encoding="utf-8")
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)
                snapshot = Path(revision["snapshot"])
                try:
                    snapshot.chmod(0o600)
                except OSError:
                    pass
                snapshot.write_text("tampered", encoding="utf-8")
                with project.connection:
                    project.connection.execute(
                        "DELETE FROM paragraphs WHERE revision_uid=?",
                        (revision["revision_uid"],),
                    )
                verification = project.verify()
                self.assertFalse(verification["ok"])
                self.assertIn("snapshot SHA-256 mismatch", verification["revisions"][0]["issues"])
                self.assertIn("paragraph completeness mismatch", verification["revisions"][0]["issues"])
                self.assertIn("structured content-map hash mismatch", verification["revisions"][0]["issues"])

    def test_cli_module_and_wrapper_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            source.write_text("# Chapter\nBody", encoding="utf-8")
            project = root / "project"
            wrapper = ROOT / "scripts/editroast.py"
            commands = (
                ["project", "create", str(project)],
                ["manuscript", "import", str(project), str(source)],
                ["manuscript", "status", str(project)],
                ["manuscript", "revisions", str(project)],
                ["manuscript", "show", str(project), "r000001"],
                ["manuscript", "verify", str(project)],
            )
            for args in commands:
                result = subprocess.run(
                    [sys.executable, str(wrapper), *args], cwd=ROOT,
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                json.loads(result.stdout)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            module = subprocess.run(
                [sys.executable, "-m", "editroast_core", "manuscript", "status", str(project)],
                cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(module.returncode, 0, module.stdout + module.stderr)


if __name__ == "__main__":
    unittest.main()
