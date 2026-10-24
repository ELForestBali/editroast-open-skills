from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "manuscript_workflow.py"
SPEC = importlib.util.spec_from_file_location("manuscript_workflow", SCRIPT)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = workflow
SPEC.loader.exec_module(workflow)


class WorkflowTests(unittest.TestCase):
    def test_prepare_resume_and_validate_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "novel.md"
            source.write_text(
                "# Chapter One\nThe bell rang seven times.\n" + "word " * 520 +
                "\n# Chapter Two\nThe harbor disappeared.\n", encoding="utf-8"
            )
            workspace = root / "run"
            result = workflow.prepare(Namespace(source=source, workspace=workspace, mode="roast", max_words=500))
            self.assertEqual(result, 0)
            manifest = workflow.read_json(workspace / "work" / "manifest.json")
            self.assertEqual(manifest["chapter_count"], 2)
            self.assertGreaterEqual(manifest["chunk_count"], 3)
            self.assertEqual(workflow.show_status(Namespace(workspace=workspace)), 0)

            for item in manifest["chunks"]:
                report = workspace / "work" / "chapter-reports" / f"{item['id']}.md"
                report.write_text(f"# Report {item['id']}\n", encoding="utf-8")
                workflow.mark_complete(Namespace(workspace=workspace, chunk_id=item["id"], report=report))

            first = manifest["chunks"][0]
            first_lines = workflow.parse_chunk_lines(workspace / first["file"])
            evidence = {
                "finding_id": "R-001",
                "chunk_id": first["id"],
                "start_line": 1,
                "end_line": 1,
                "quote": first_lines[0],
                "severity": "major",
            }
            (workspace / "work" / "evidence.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            (workspace / "output" / "manuscript-roast-report.md").write_text("# Final\n", encoding="utf-8")
            self.assertEqual(workflow.validate(Namespace(workspace=workspace, require_final=True)), 0)

            evidence["quote"] = "invented quote"
            (workspace / "work" / "evidence.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            self.assertEqual(workflow.validate(Namespace(workspace=workspace, require_final=True)), 1)

    def test_extracts_plain_docx_without_external_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "book.docx"
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapter One</w:t></w:r></w:p>
<w:p><w:r><w:t>A clean paragraph.</w:t></w:r></w:p>
</w:body></w:document>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document_xml)
            lines = workflow.extract_source(path)
            self.assertEqual([line.text for line in lines], ["Chapter One", "A clean paragraph."])
            self.assertTrue(lines[0].heading)

    def test_refuses_to_overwrite_nonempty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("Chapter One\nText", encoding="utf-8")
            workspace = root / "run"
            workspace.mkdir()
            (workspace / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.prepare(Namespace(source=source, workspace=workspace, mode="proofreading", max_words=500))


if __name__ == "__main__":
    unittest.main()

