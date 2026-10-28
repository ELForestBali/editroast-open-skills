from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import build_release


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PackagingTests(unittest.TestCase):
    def test_build_is_deterministic_and_dependencies_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_paths = build_release.build_all(ROOT, Path(first))
            second_paths = build_release.build_all(ROOT, Path(second))

            self.assertEqual([path.name for path in first_paths], [path.name for path in second_paths])
            self.assertEqual([digest(path) for path in first_paths], [digest(path) for path in second_paths])
            self.assertEqual(
                (Path(first) / "SHA256SUMS.txt").read_text(encoding="utf-8"),
                (Path(second) / "SHA256SUMS.txt").read_text(encoding="utf-8"),
            )

            for path in first_paths:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    self.assertFalse(build_release.archive_path_errors(names))
                    if "literary-translation" in path.name or "book-production" in path.name:
                        skill_file = next(name for name in names if name.endswith("/SKILL.md"))
                        self.assertIn("BETA", archive.read(skill_file).decode("utf-8"))

    def test_built_artifacts_install_for_codex_and_claude(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            archives = build_release.build_all(ROOT, dist)
            individual = {
                name: next(path for path in archives if path.name.startswith(f"editroast-{name}-v"))
                for name in build_release.SKILLS
            }

            for skill, archive in individual.items():
                extracted = root / f"extracted-{skill}"
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(extracted)
                package_root = next(extracted.iterdir())
                for target, folder in (("codex", ".agents/skills"), ("claude", ".claude/skills")):
                    project = root / f"{target}-{skill}"
                    project.mkdir()
                    command = [
                        sys.executable,
                        str(package_root / "install.py"),
                        "--target",
                        target,
                        "--scope",
                        "project",
                        "--project-dir",
                        str(project),
                    ]
                    if not (skill == "manuscript-roast" and target == "codex"):
                        command.extend(("--skill", skill))
                    result = subprocess.run(
                        command,
                        cwd=package_root,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    installed = project / folder / skill
                    self.assertTrue((installed / "SKILL.md").is_file())
                    self.assertTrue((installed / "scripts/editroast_core/__init__.py").is_file())
                    self.assertTrue((installed / "templates/book-map.md").is_file())

    def test_source_installer_preserves_v1_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "install.py"),
                    "--target",
                    "codex",
                    "--scope",
                    "project",
                    "--project-dir",
                    str(project),
                    "--skill",
                    "roast",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(
                (project / ".agents/skills/manuscript-roast/scripts/manuscript_workflow.py").is_file()
            )

    def test_archive_path_guard_rejects_cross_platform_traversal(self) -> None:
        unsafe = ["../escape.txt", "/absolute.txt", "C:/drive.txt", "folder\\escape.txt"]
        self.assertEqual(build_release.archive_path_errors(unsafe), unsafe)

    def test_only_one_maintained_core_implementation(self) -> None:
        expected = {
            ROOT / "scripts/editroast_state.py",
            ROOT / "src/editroast_core/editroast_state.py",
        }
        actual = {
            path
            for path in ROOT.rglob("editroast_state.py")
            if ".tmp-" not in path.as_posix() and "dist" not in path.parts
        }
        self.assertEqual(actual, expected)

    def test_synthetic_fixtures_cover_english_and_russian(self) -> None:
        from editroast_core import manuscript_workflow

        for name in ("synthetic-en.md", "synthetic-ru.md"):
            lines = manuscript_workflow.extract_source(ROOT / "tests/fixtures" / name)
            chapters = manuscript_workflow.split_chapters(lines)
            self.assertEqual(len(chapters), 3)


if __name__ == "__main__":
    unittest.main()
