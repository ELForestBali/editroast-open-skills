from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.manuscript_state import ManuscriptProject  # noqa: E402


FIRST = """# Harbor
The long red boat crossed the silent harbor before dawn.
Mara counted seven lamps.
Nobody was waiting.
Repeat line.
Repeat line.
# Home
Jon returned home."""

SECOND = """# Home Revised
Jon returned home.
# Harbor
The long red boat crossed the silent harbor.
It arrived before dawn.
Mara counted seven lamps. Nobody was waiting.
Repeat line.
Repeat line.
Repeat line."""


class RevisionDiffTests(unittest.TestCase):
    def _two_revisions(self, root: Path) -> tuple[Path, dict, dict]:
        source = root / "book.md"
        project_path = root / "project"
        source.write_text(FIRST, encoding="utf-8")
        with ManuscriptProject.create(project_path) as project:
            first = project.import_manuscript(source)
            source.write_text(SECOND, encoding="utf-8")
            second = project.import_manuscript(source)
        return project_path, first, second

    def test_structural_diff_detects_moves_splits_merges_and_chapter_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, first, second = self._two_revisions(root)
            with ManuscriptProject.open(project_path) as project:
                payload = project.revision_diff(second["revision_uid"], include_text=True)
                self.assertEqual(payload["from_revision"], first["revision_uid"])
                self.assertGreaterEqual(payload["summary"].get("moved", 0), 1)
                self.assertGreaterEqual(payload["summary"].get("split", 0), 1)
                self.assertGreaterEqual(payload["summary"].get("merged", 0), 1)
                self.assertGreaterEqual(payload["summary"].get("ambiguous", 0), 1)

                groups = payload["groups"]
                split = next(group for group in groups if group["relation_type"] == "split")
                merge = next(group for group in groups if group["relation_type"] == "merged")
                self.assertEqual(len([item for item in split["members"] if item["side"] == "before"]), 1)
                self.assertEqual(len([item for item in split["members"] if item["side"] == "after"]), 2)
                self.assertEqual(len([item for item in merge["members"] if item["side"] == "before"]), 2)
                self.assertEqual(len([item for item in merge["members"] if item["side"] == "after"]), 1)

                first_jon = project.connection.execute(
                    "SELECT stable_id FROM paragraphs WHERE revision_uid=? AND text='Jon returned home.'",
                    (first["revision_uid"],),
                ).fetchone()[0]
                second_jon = project.connection.execute(
                    "SELECT stable_id FROM paragraphs WHERE revision_uid=? AND text='Jon returned home.'",
                    (second["revision_uid"],),
                ).fetchone()[0]
                self.assertEqual(first_jon, second_jon)

                first_harbor = project.connection.execute(
                    "SELECT stable_id FROM chapters WHERE revision_uid=? AND title='Harbor'",
                    (first["revision_uid"],),
                ).fetchone()[0]
                second_harbor = project.connection.execute(
                    "SELECT stable_id FROM chapters WHERE revision_uid=? AND title='Harbor'",
                    (second["revision_uid"],),
                ).fetchone()[0]
                self.assertEqual(first_harbor, second_harbor)
                self.assertTrue(any(
                    group["entity_type"] == "chapter" and group["relation_type"] in {"moved", "changed"}
                    for group in groups
                ))

                without_text = project.revision_diff(second["revision_uid"])
                self.assertTrue(all(
                    "text" not in member
                    for group in without_text["groups"] for member in group["members"]
                ))

    def test_ambiguous_candidates_have_confidence_and_manual_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, second = self._two_revisions(root)
            with ManuscriptProject.open(project_path) as project:
                queue = project.reconciliation_queue(second["revision_uid"])
                repeated = next(group for group in queue if len(group["candidates"]) >= 2)
                candidate = repeated["candidates"][0]
                unresolved_before = project.status()["unresolved_reconciliation_count"]
                self.assertGreater(candidate["confidence"], 0)
                self.assertIn(candidate["confidence_label"], {"low", "medium", "high"})

                resolved = project.reconcile(
                    second["revision_uid"], repeated["group_uid"],
                    candidate_uid=candidate["candidate_uid"], actor="test-author",
                    note="Synthetic identity selection",
                )
                self.assertEqual(resolved["status"], "manually_resolved")
                self.assertEqual(resolved["resolution"]["actor"], "test-author")
                self.assertEqual(
                    project.status()["unresolved_reconciliation_count"], unresolved_before - 1
                )
                self.assertEqual(len(project.reconciliation_history(
                    second["revision_uid"], repeated["group_uid"]
                )), 1)

                reopened = project.reopen_reconciliation(
                    second["revision_uid"], repeated["group_uid"],
                    actor="test-author", note="Reconsidered",
                )
                self.assertEqual(reopened["status"], "needs_reconciliation")
                self.assertEqual(
                    project.status()["unresolved_reconciliation_count"], unresolved_before
                )
                history = project.reconciliation_history(second["revision_uid"], repeated["group_uid"])
                self.assertEqual([event["action"] for event in history], ["resolve", "reopen"])
                self.assertEqual(history[1]["supersedes_event_uid"], history[0]["event_uid"])

                # Reconciliation records links only; immutable source and paragraph IDs remain unchanged.
                self.assertTrue(project.verify()["ok"])

    def test_lexical_semantic_risks_are_flags_not_intent_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "risk.txt"
            project_path = root / "project"
            source.write_text("Alice is not 10 years old.", encoding="utf-8")
            with ManuscriptProject.create(project_path) as project:
                project.import_manuscript(source)
                source.write_text("Alicia is now 11 years old.", encoding="utf-8")
                second = project.import_manuscript(source)
                changed = next(
                    group for group in project.revision_diff(second["revision_uid"], include_text=True)["groups"]
                    if group["entity_type"] == "paragraph"
                )
                self.assertEqual(changed["effective_relation_type"], "ambiguous")
                self.assertEqual(changed["status"], "needs_reconciliation")
                signals = changed["semantic_signals"]
                self.assertTrue(signals["numbers_changed"])
                self.assertTrue(signals["negation_tokens_changed"])
                self.assertTrue(signals["capitalized_terms_changed"])
                self.assertEqual(signals["semantic_interpretation"], "not_evaluated")

    def test_ambiguous_chapter_rename_exposes_ranked_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "chapters.md"
            project_path = root / "project"
            source.write_text("# Red Harbor\nOne.\n# Blue Harbor\nTwo.", encoding="utf-8")
            with ManuscriptProject.create(project_path) as project:
                project.import_manuscript(source)
                source.write_text("# Green Harbor\nOne.\n# Gold Harbor\nTwo.", encoding="utf-8")
                second = project.import_manuscript(source)
                unresolved = [
                    group for group in project.reconciliation_queue(second["revision_uid"])
                    if group["entity_type"] == "chapter"
                ]
                self.assertTrue(unresolved)
                self.assertTrue(all(group["candidates"] for group in unresolved))
                self.assertTrue(all(
                    candidate["relation_type"] == "changed"
                    for group in unresolved for candidate in group["candidates"]
                ))
                self.assertTrue(project.verify()["ok"])

    def test_effective_mapping_conflicts_and_reopen_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, second = self._two_revisions(root)
            with ManuscriptProject.open(project_path) as project:
                queue = project.reconciliation_queue(second["revision_uid"])
                shared: tuple[dict, dict, str] | None = None
                for left_index, left in enumerate(queue):
                    left_ids = {
                        before_id
                        for candidate in left["candidates"]
                        for before_id in candidate["before_stable_ids"]
                    }
                    for right in queue[left_index + 1:]:
                        right_ids = {
                            before_id
                            for candidate in right["candidates"]
                            for before_id in candidate["before_stable_ids"]
                        }
                        overlap = sorted(left_ids & right_ids)
                        if overlap:
                            shared = left, right, overlap[0]
                            break
                    if shared:
                        break
                self.assertIsNotNone(shared)
                left, right, old_id = shared  # type: ignore[misc]

                left_candidate = next(
                    candidate for candidate in left["candidates"]
                    if old_id in candidate["before_stable_ids"]
                )
                right_candidate = next(
                    candidate for candidate in right["candidates"]
                    if old_id in candidate["before_stable_ids"]
                )
                project.reconcile(
                    second["revision_uid"], left["group_uid"],
                    candidate_uid=left_candidate["candidate_uid"], actor="test-author",
                )
                with self.assertRaisesRegex(
                    ValueError, rf"before {old_id} is claimed by {left['group_uid']}"
                ):
                    project.reconcile(
                        second["revision_uid"], right["group_uid"],
                        candidate_uid=right_candidate["candidate_uid"], actor="test-author",
                    )

                project.reopen_reconciliation(
                    second["revision_uid"], left["group_uid"], actor="test-author"
                )
                project.reconcile(
                    second["revision_uid"], right["group_uid"],
                    candidate_uid=right_candidate["candidate_uid"], actor="test-author",
                )
                with self.assertRaisesRegex(
                    ValueError, rf"before {old_id} is claimed by {right['group_uid']}"
                ):
                    project.reconcile(
                        second["revision_uid"], left["group_uid"],
                        candidate_uid=left_candidate["candidate_uid"], actor="test-author",
                    )

    def test_effective_mapping_rejects_automatic_overlap_but_allows_split_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, second = self._two_revisions(root)
            with ManuscriptProject.open(project_path) as project:
                payload = project.revision_diff(second["revision_uid"])
                automatic = next(
                    group for group in payload["groups"]
                    if group["entity_type"] == "paragraph"
                    and group["status"] == "auto"
                    and any(member["side"] == "before" for member in group["members"])
                )
                unresolved = next(
                    group for group in payload["groups"]
                    if group["entity_type"] == "paragraph"
                    and group["status"] == "needs_reconciliation"
                    and group["candidates"]
                )
                automatic_before = next(
                    member["stable_id"] for member in automatic["members"]
                    if member["side"] == "before"
                )
                unresolved_after = next(
                    member["stable_id"] for member in unresolved["members"]
                    if member["side"] == "after"
                )
                with self.assertRaisesRegex(
                    ValueError,
                    rf"before {automatic_before} is claimed by {automatic['group_uid']}",
                ):
                    project.reconcile(
                        second["revision_uid"], unresolved["group_uid"],
                        relation_type="changed", before_ids=[automatic_before],
                        after_ids=[unresolved_after], actor="test-author",
                    )

                split = next(
                    group for group in payload["groups"]
                    if group["entity_type"] == "paragraph" and group["relation_type"] == "split"
                )
                split_before = [
                    member["stable_id"] for member in split["members"] if member["side"] == "before"
                ]
                split_after = [
                    member["stable_id"] for member in split["members"] if member["side"] == "after"
                ]
                resolved_split = project.reconcile(
                    second["revision_uid"], split["group_uid"], relation_type="split",
                    before_ids=split_before, after_ids=split_after, actor="test-author",
                )
                self.assertEqual(resolved_split["effective_relation_type"], "split")
                self.assertEqual(
                    resolved_split["resolution"]["after_stable_ids"], split_after
                )

    def test_schema_v3_backfills_saved_pr02_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, second = self._two_revisions(root)
            database = project_path / "editroast.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    DROP TABLE reconciliation_events;
                    DROP TABLE reconciliation_candidates;
                    DROP TABLE revision_diff_members;
                    DROP TABLE revision_diff_groups;
                    DELETE FROM schema_migrations WHERE version=3;
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with ManuscriptProject.open(project_path) as migrated:
                self.assertEqual(migrated.status()["schema_version"], 5)
                payload = migrated.revision_diff(second["revision_uid"])
                self.assertTrue(payload["groups"])
                self.assertTrue(any(group["status"] == "legacy_auto" for group in payload["groups"]))
                self.assertTrue(migrated.verify()["ok"])

    def test_cli_diff_queue_and_candidate_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, second = self._two_revisions(root)
            script = ROOT / "scripts/editroast.py"

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(script), *arguments], cwd=ROOT,
                    capture_output=True, text=True, check=False,
                )

            diff = run("manuscript", "diff", str(project_path), second["revision_uid"])
            self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
            self.assertNotIn("The long red boat", diff.stdout)
            queue = run("manuscript", "reconcile-list", str(project_path), second["revision_uid"])
            self.assertEqual(queue.returncode, 0, queue.stdout + queue.stderr)
            unresolved = json.loads(queue.stdout)
            group = next(item for item in unresolved if item["candidates"])
            candidate = group["candidates"][0]
            resolution = run(
                "manuscript", "reconcile", str(project_path), second["revision_uid"], group["group_uid"],
                "--candidate", candidate["candidate_uid"], "--actor", "cli-test",
            )
            self.assertEqual(resolution.returncode, 0, resolution.stdout + resolution.stderr)
            self.assertEqual(json.loads(resolution.stdout)["status"], "manually_resolved")
            history = run(
                "manuscript", "reconcile-history", str(project_path), second["revision_uid"],
                "--group", group["group_uid"],
            )
            self.assertEqual(history.returncode, 0, history.stdout + history.stderr)
            self.assertEqual(len(json.loads(history.stdout)), 1)


if __name__ == "__main__":
    unittest.main()
