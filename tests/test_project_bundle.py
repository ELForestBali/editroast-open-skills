from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from editroast_core.manuscript_state import ManuscriptProject  # noqa: E402
from editroast_core.project_bundle import (  # noqa: E402
    export_project_bundle,
    import_project_bundle,
    inspect_project_bundle,
)


def make_project(root: Path) -> tuple[Path, dict, str]:
    source = root / "book.md"
    source.write_text(
        "# Chapter One\nA synthetic sentence.\n# Chapter Two\nAnother safe sentence.",
        encoding="utf-8",
    )
    project_path = root / "source-project"
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    with ManuscriptProject.create(
        project_path, title="Portable Synthetic", author="Test Author", language="en"
    ) as project:
        revision = project.import_manuscript(source)
        paragraph = project.connection.execute(
            """SELECT stable_id FROM paragraphs
               WHERE revision_uid=? AND text='A synthetic sentence.'""",
            (revision["revision_uid"],),
        ).fetchone()[0]
        project.record_finding(
            "F-PORTABLE",
            revision["revision_uid"],
            quote="A synthetic sentence.",
            recommendation="Keep the synthetic evidence inspectable.",
            paragraph_stable_id=paragraph,
            anchor={"line": 2},
        )
        project.record_decision(
            "D-PORTABLE",
            "editorial",
            revision_uid=revision["revision_uid"],
            payload={"choice": "keep", "api_key": secret, "source_path": str(source)},
        )
        project.decide("D-PORTABLE", "approved", selected={"choice": "keep"})
        project.add_correction(
            "C-PORTABLE",
            revision["revision_uid"],
            paragraph_stable_id=paragraph,
            exact_before="synthetic",
            replacement="sample",
            change_class="mechanical",
            finding_id="F-PORTABLE",
        )
        project.decide_corrections(
            "approved", correction_ids=["C-PORTABLE"], author_confirmed=True
        )
        project.create_run(
            "portable-run",
            revision["revision_uid"],
            run_type="manuscript-roast",
            provenance={"api_key": secret, "workspace": str(project_path)},
        )
        claim = project.claim_run_unit("portable-run", worker_id="worker")["next_unit"]
        result = project_path / "reports" / f"{claim['unit_id']}.md"
        result.write_text("Synthetic chapter result.\n", encoding="utf-8")
        project.complete_run_unit(
            "portable-run",
            claim["unit_id"],
            attempt_uid=claim["active_attempt_uid"],
            result=result,
        )
        project.claim_run_unit("portable-run", worker_id="interrupted-worker")
    return project_path, revision, secret


class ProjectBundleTests(unittest.TestCase):
    def test_round_trip_preserves_versions_findings_approvals_and_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_project, revision, secret = make_project(root)
            bundle_path = root / "portable.editroast"
            with ManuscriptProject.open(source_project) as project:
                exported = export_project_bundle(project, bundle_path)
            self.assertEqual(exported["revision_ids"], [revision["revision_uid"]])
            self.assertEqual(exported["finding_ids"], ["F-PORTABLE"])
            self.assertGreaterEqual(exported["sanitization"]["redacted_values"], 3)

            inspected = inspect_project_bundle(bundle_path)
            self.assertEqual(inspected["format"], "editroast-project")
            self.assertEqual(inspected["bundle_sha256"], hashlib.sha256(bundle_path.read_bytes()).hexdigest())
            with zipfile.ZipFile(bundle_path) as archive:
                payload = b"\n".join(archive.read(info) for info in archive.infolist())
            self.assertNotIn(secret.encode(), payload)
            self.assertNotIn(str(root).encode(), payload)
            self.assertNotIn(str(Path.home()).encode(), payload)

            restored_path = root / "restored-project"
            imported = import_project_bundle(bundle_path, restored_path)
            self.assertEqual(imported["revision_ids"], [revision["revision_uid"]])
            self.assertEqual(imported["finding_ids"], ["F-PORTABLE"])
            with ManuscriptProject.open(restored_path) as restored:
                self.assertEqual(
                    [item["revision_uid"] for item in restored.revisions()],
                    [revision["revision_uid"]],
                )
                finding = restored.connection.execute(
                    "SELECT exact_quote,recommendation,status FROM editorial_findings WHERE finding_id=?",
                    ("F-PORTABLE",),
                ).fetchone()
                self.assertEqual(
                    tuple(finding),
                    (
                        "A synthetic sentence.",
                        "Keep the synthetic evidence inspectable.",
                        "pending",
                    ),
                )
                self.assertEqual(
                    restored.connection.execute(
                        "SELECT status FROM author_decisions WHERE decision_id='D-PORTABLE'"
                    ).fetchone()[0],
                    "approved",
                )
                self.assertEqual(restored.correction("C-PORTABLE")["status"], "approved")
                run = restored.run_status("portable-run")
                self.assertEqual(run["counts"], {
                    "pending": 0, "in_progress": 1, "completed": 1, "failed": 0
                })
                self.assertEqual(run["verified_completed"], 1)
                resumed = restored.resume_run("portable-run")
                self.assertEqual(resumed["counts"]["completed"], 1)
                self.assertEqual(resumed["counts"]["pending"], 1)

    def test_export_refuses_corrupt_results_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, _ = make_project(root)
            with ManuscriptProject.open(project_path) as project:
                result_path = project.connection.execute(
                    "SELECT result_path FROM run_units WHERE status='completed'"
                ).fetchone()[0]
                (project.root / result_path).write_text("tampered", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Project is not safe to export"):
                    export_project_bundle(project, root / "bad.editroast")
                existing = root / "existing.editroast"
                existing.write_bytes(b"do not overwrite")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    export_project_bundle(project, existing)
                self.assertEqual(existing.read_bytes(), b"do not overwrite")

    def test_tampered_or_traversing_bundle_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, _ = make_project(root)
            bundle = root / "valid.editroast"
            with ManuscriptProject.open(project_path) as project:
                export_project_bundle(project, bundle)

            tampered = root / "tampered.editroast"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
                for info in source.infolist():
                    data = source.read(info)
                    if info.filename == "project.json":
                        data += b"tampered"
                    target.writestr(info, data)
            destination = root / "tampered-import"
            with self.assertRaisesRegex(ValueError, "(?:size|SHA-256) mismatch"):
                import_project_bundle(tampered, destination)
            self.assertFalse(destination.exists())

            existing_destination = root / "existing-project"
            existing_destination.mkdir()
            marker = existing_destination / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                import_project_bundle(bundle, existing_destination)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

            traversal = root / "traversal.editroast"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("manifest.json", "{}")
                archive.writestr("../escape.txt", "unsafe")
            with self.assertRaisesRegex(ValueError, "Unsafe bundle path"):
                inspect_project_bundle(traversal)
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_future_bundle_version_and_secret_payload_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, _, _ = make_project(root)
            bundle = root / "valid.editroast"
            with ManuscriptProject.open(project_path) as project:
                export_project_bundle(project, bundle)

            future = root / "future.editroast"
            with zipfile.ZipFile(bundle) as source:
                manifest = json.loads(source.read("manifest.json"))
                manifest["schema_version"] = 999
                with zipfile.ZipFile(future, "w") as target:
                    for info in source.infolist():
                        data = source.read(info)
                        if info.filename == "manifest.json":
                            data = json.dumps(manifest).encode()
                        target.writestr(info, data)
            with self.assertRaisesRegex(ValueError, "newer than supported"):
                inspect_project_bundle(future)

            secret_bundle = root / "secret.editroast"
            secret = b"sk-abcdefghijklmnopqrstuvwxyz123456"
            project_json = secret
            manifest = {
                "format": "editroast-project",
                "format_version": 1,
                "schema_version": 5,
                "project_id": "project-test",
                "revision_ids": [],
                "finding_ids": [],
                "decision_ids": [],
                "application_ids": [],
                "run_ids": [],
                "entries": [
                    {"path": "project.json", "size": len(project_json), "sha256": hashlib.sha256(project_json).hexdigest()},
                    {"path": "editroast.sqlite", "size": 0, "sha256": hashlib.sha256(b"").hexdigest()},
                ],
            }
            with zipfile.ZipFile(secret_bundle, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("project.json", project_json)
                archive.writestr("editroast.sqlite", b"")
            with self.assertRaisesRegex(ValueError, "credential-like secret"):
                inspect_project_bundle(secret_bundle)

    def test_cli_export_inspect_and_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path, revision, _ = make_project(root)
            bundle = root / "cli.editroast"
            restored = root / "cli-restored"
            wrapper = ROOT / "scripts" / "editroast.py"

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(wrapper), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            exported = run("bundle", "export", str(project_path), str(bundle))
            self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
            inspected = run("bundle", "inspect", str(bundle))
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["revision_ids"], [revision["revision_uid"]])
            imported = run("bundle", "import", str(bundle), str(restored))
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            self.assertTrue((restored / "editroast.sqlite").is_file())


if __name__ == "__main__":
    unittest.main()
