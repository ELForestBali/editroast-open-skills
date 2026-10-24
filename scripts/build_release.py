#!/usr/bin/env python3
"""Build reproducible EditRoast skill release ZIP files."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


VERSION = "1.0.0"
PACKS = {
    "manuscript-roast": ("manuscript-roast",),
    "manuscript-proofreading": ("manuscript-proofreading",),
    "complete-pack": ("manuscript-roast", "manuscript-proofreading"),
}
ROOT_FILES = ("README.md", "LICENSE", "CHANGELOG.md", "install.py")


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.zip"))


def build_pack(repo: Path, dist: Path, pack_name: str, skills: tuple[str, ...]) -> Path:
    with tempfile.TemporaryDirectory(prefix="editroast-release-") as temporary:
        staging_root = Path(temporary) / f"editroast-{pack_name}-v{VERSION}"
        staging_root.mkdir()
        for name in ROOT_FILES:
            shutil.copy2(repo / name, staging_root / name)
        (staging_root / "skills").mkdir()
        for skill in skills:
            copy_tree(repo / "skills" / skill, staging_root / "skills" / skill)
        copy_tree(repo / "scripts", staging_root / "scripts")
        copy_tree(repo / "templates", staging_root / "templates")
        copy_tree(repo / "examples", staging_root / "examples")
        copy_tree(repo / "docs", staging_root / "docs")

        archive = dist / f"editroast-{pack_name}-v{VERSION}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for path in sorted(staging_root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(staging_root.parent).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(2026, 10, 24, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                bundle.writestr(info, path.read_bytes())
        return archive


def verify_archive(path: Path, expected_skills: tuple[str, ...]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError(f"{path.name}: expected one top-level folder")
        root = next(iter(roots))
        required = {f"{root}/README.md", f"{root}/LICENSE", f"{root}/install.py",
                    f"{root}/scripts/manuscript_workflow.py"}
        required.update(f"{root}/skills/{skill}/SKILL.md" for skill in expected_skills)
        missing = required - names
        if missing:
            raise ValueError(f"{path.name}: missing {sorted(missing)}")
        forbidden_markers = ("Authors/", "node_modules/", ".env", "send_report", "system_prozarka")
        bad = [name for name in names if any(marker in name for marker in forbidden_markers)]
        if bad:
            raise ValueError(f"{path.name}: forbidden content {bad[:5]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    dist = (args.dist or repo / "dist").resolve()
    dist.mkdir(parents=True, exist_ok=True)
    for pack_name, skills in PACKS.items():
        archive = build_pack(repo, dist, pack_name, skills)
        verify_archive(archive, skills)
        print(f"Built {archive}")


if __name__ == "__main__":
    main()

