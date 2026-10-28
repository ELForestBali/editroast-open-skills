"""Tests for source-bound ER-020 inventory and editorial evidence validation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.book_map import inventory_book, validate_book_map
from editroast_core.manuscript_state import ManuscriptProject


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

    def test_snapshot_tamper_is_rejected(self):
        book = inventory_book(self.project, self.revision)
        Path(self.project.revision(self.revision)["snapshot"]).write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "integrity"):
            validate_book_map(self.project, self.revision, book)


if __name__ == "__main__":
    unittest.main()
