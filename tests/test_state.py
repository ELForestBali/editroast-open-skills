from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/editroast_state.py"


class StateCompatibilityTests(unittest.TestCase):
    def test_revision_decision_and_translation_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "book.txt"
            source.write_text("First synthetic revision", encoding="utf-8")
            project = root / "project"

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(SCRIPT), "--project", str(project), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            first = run("import", str(source))
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            source.write_text("Second synthetic revision", encoding="utf-8")
            second = run("import", str(source))
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["parent"], 1)

            self.assertEqual(
                run(
                    "decision",
                    "--id",
                    "T-001",
                    "--kind",
                    "idiom",
                    "--source",
                    "synthetic phrase",
                    "--alternatives",
                    '["literal", "adapted"]',
                ).returncode,
                0,
            )
            self.assertEqual(run("translation-gate").returncode, 2)
            self.assertNotEqual(
                run(
                    "decide",
                    "--id",
                    "T-001",
                    "--status",
                    "approved",
                    "--selected",
                    "unlisted",
                ).returncode,
                0,
            )
            self.assertEqual(
                run(
                    "decide",
                    "--id",
                    "T-001",
                    "--status",
                    "approved",
                    "--selected",
                    "adapted",
                ).returncode,
                0,
            )
            self.assertEqual(run("translation-gate").returncode, 0)
            self.assertEqual(run("export-decisions").returncode, 0)
            self.assertTrue((project / "translation_decisions.csv").is_file())


if __name__ == "__main__":
    unittest.main()
