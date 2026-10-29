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


def make_manuscript(path: Path, chapter_count: int = 4) -> None:
    path.write_text(
        "\n".join(
            line
            for number in range(1, chapter_count + 1)
            for line in (f"# Chapter {number}", f"Synthetic body {number}.")
        ),
        encoding="utf-8",
    )


def write_result(project_path: Path, unit_id: str, text: str | None = None) -> Path:
    result = project_path / "work" / "runs" / f"{unit_id}.md"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(text or f"Verified result for {unit_id}\n", encoding="utf-8")
    return result


class ResumableRunTests(unittest.TestCase):
    def test_resume_after_halfway_crash_preserves_completed_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            make_manuscript(source)
            project_path = root / "project"
            completed_ids: list[str] = []

            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)
                created = project.create_run(
                    "roast-1", revision["revision_uid"], run_type="manuscript-roast"
                )
                self.assertEqual(created["unit_count"], 4)
                for _ in range(2):
                    claim = project.claim_run_unit("roast-1", worker_id="worker-a")["next_unit"]
                    completed_ids.append(claim["unit_id"])
                    project.complete_run_unit(
                        "roast-1",
                        claim["unit_id"],
                        attempt_uid=claim["active_attempt_uid"],
                        result=write_result(project_path, claim["unit_id"]),
                    )
                abandoned = project.claim_run_unit("roast-1", worker_id="worker-a")["next_unit"]
                self.assertEqual(project.run_status("roast-1")["progress_percent"], 50.0)

            with ManuscriptProject.open(project_path) as project:
                resumed = project.resume_run("roast-1")
                self.assertEqual(resumed["counts"]["completed"], 2)
                self.assertEqual(resumed["counts"]["pending"], 2)
                claim = project.claim_run_unit("roast-1", worker_id="worker-b")["next_unit"]
                self.assertEqual(claim["unit_id"], abandoned["unit_id"])
                self.assertNotIn(claim["unit_id"], completed_ids)
                while claim:
                    project.complete_run_unit(
                        "roast-1",
                        claim["unit_id"],
                        attempt_uid=claim["active_attempt_uid"],
                        result=write_result(project_path, claim["unit_id"]),
                    )
                    claim = project.claim_run_unit("roast-1", worker_id="worker-b")["next_unit"]

                verification = project.verify_run("roast-1")
                self.assertTrue(verification["ok"])
                self.assertEqual(verification["progress_percent"], 100.0)
                self.assertEqual(
                    project.connection.execute(
                        "SELECT attempt_count FROM run_units WHERE run_id=? AND unit_id=?",
                        ("roast-1", completed_ids[0]),
                    ).fetchone()[0],
                    1,
                )
                self.assertTrue(project.verify()["ok"])

    def test_completion_retry_is_idempotent_and_stale_attempt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            make_manuscript(source, 1)
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)
                project.create_run("proof-1", revision["revision_uid"], run_type="proofreading")
                claim = project.claim_run_unit("proof-1", worker_id="worker")["next_unit"]
                result = write_result(project_path, claim["unit_id"])
                first = project.complete_run_unit(
                    "proof-1", claim["unit_id"],
                    attempt_uid=claim["active_attempt_uid"], result=result,
                )
                self.assertTrue(first["coverage_complete"])
                repeated = project.complete_run_unit(
                    "proof-1", claim["unit_id"],
                    attempt_uid=claim["active_attempt_uid"], result=result,
                )
                self.assertTrue(repeated["idempotent"])
                self.assertEqual(
                    project.connection.execute(
                        "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='unit.completed'",
                        ("proof-1",),
                    ).fetchone()[0],
                    1,
                )
                with self.assertRaisesRegex(ValueError, "different result"):
                    project.complete_run_unit(
                        "proof-1", claim["unit_id"],
                        attempt_uid="stale-attempt", result=result,
                    )

    def test_tampered_result_never_reports_full_coverage_and_can_be_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            make_manuscript(source, 2)
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)
                project.create_run("roast-2", revision["revision_uid"], run_type="roast")
                results: dict[str, Path] = {}
                for _ in range(2):
                    claim = project.claim_run_unit("roast-2", worker_id="worker")["next_unit"]
                    results[claim["unit_id"]] = write_result(project_path, claim["unit_id"])
                    project.complete_run_unit(
                        "roast-2", claim["unit_id"],
                        attempt_uid=claim["active_attempt_uid"], result=results[claim["unit_id"]],
                    )
                damaged_id = next(iter(results))
                results[damaged_id].write_text("tampered\n", encoding="utf-8")

                status = project.run_status("roast-2")
                self.assertEqual(status["status"], "completed")
                self.assertEqual(status["effective_status"], "integrity_error")
                self.assertEqual(status["progress_percent"], 50.0)
                self.assertFalse(status["coverage_complete"])
                self.assertFalse(project.verify()["ok"])

                resumed = project.resume_run("roast-2")
                self.assertEqual(resumed["counts"]["pending"], 1)
                self.assertEqual(resumed["counts"]["completed"], 1)
                repair = project.claim_run_unit("roast-2", worker_id="repair")["next_unit"]
                self.assertEqual(repair["unit_id"], damaged_id)
                repaired_result = write_result(project_path, damaged_id, "repaired\n")
                project.complete_run_unit(
                    "roast-2", damaged_id,
                    attempt_uid=repair["active_attempt_uid"], result=repaired_result,
                )
                self.assertTrue(project.verify_run("roast-2")["ok"])

    def test_checkpoint_failure_paths_and_project_local_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            make_manuscript(source, 2)
            project_path = root / "project"
            with ManuscriptProject.create(project_path) as project:
                revision = project.import_manuscript(source)
                project.create_run("quota-1", revision["revision_uid"], run_type="roast")
                claim = project.claim_run_unit("quota-1", worker_id="worker")["next_unit"]
                outside = root / "outside.md"
                outside.write_text("outside", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "inside the project"):
                    project.complete_run_unit(
                        "quota-1", claim["unit_id"],
                        attempt_uid=claim["active_attempt_uid"], result=outside,
                    )
                project.checkpoint_run(
                    "quota-1", "blocked_by_usage_limit", reason="Provider allowance reached"
                )
                with self.assertRaisesRegex(ValueError, "resume it"):
                    project.claim_run_unit("quota-1", worker_id="worker")
                project.resume_run("quota-1")
                reclaimed = project.claim_run_unit("quota-1", worker_id="worker-2")["next_unit"]
                self.assertEqual(reclaimed["unit_id"], claim["unit_id"])
                self.assertNotEqual(reclaimed["active_attempt_uid"], claim["active_attempt_uid"])
                with self.assertRaisesRegex(ValueError, "Stale or invalid"):
                    project.complete_run_unit(
                        "quota-1", claim["unit_id"],
                        attempt_uid=claim["active_attempt_uid"],
                        result=write_result(project_path, claim["unit_id"]),
                    )

    def test_schema_v5_migrates_schema_v4_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "project"
            with ManuscriptProject.create(project_path):
                pass
            connection = sqlite3.connect(project_path / "editroast.sqlite")
            try:
                connection.executescript(
                    """
                    DROP TABLE run_events;
                    DROP TABLE run_units;
                    DELETE FROM schema_migrations WHERE version=5;
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with ManuscriptProject.open(project_path) as migrated:
                self.assertEqual(migrated.status()["schema_version"], 7)
                self.assertEqual(migrated.status()["run_counts"], {})
                self.assertTrue(migrated.verify()["ok"])

    def test_cli_run_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.md"
            make_manuscript(source, 1)
            project_path = root / "project"
            wrapper = ROOT / "scripts" / "editroast.py"

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(wrapper), *arguments],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )

            self.assertEqual(run("project", "create", str(project_path)).returncode, 0)
            imported = run("manuscript", "import", str(project_path), str(source))
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            revision = json.loads(imported.stdout)["revision_uid"]
            created = run(
                "run", "create", str(project_path), "cli-run", revision,
                "--type", "roast",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            claimed = run(
                "run", "claim", str(project_path), "cli-run", "--worker", "cli-worker"
            )
            self.assertEqual(claimed.returncode, 0, claimed.stdout + claimed.stderr)
            unit = json.loads(claimed.stdout)["next_unit"]
            result = write_result(project_path, unit["unit_id"])
            completed = run(
                "run", "complete", str(project_path), "cli-run", unit["unit_id"],
                "--attempt", unit["active_attempt_uid"], "--result", str(result),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            verified = run("run", "verify", str(project_path), "cli-run")
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            self.assertTrue(json.loads(verified.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
