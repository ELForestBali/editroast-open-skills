#!/usr/bin/env python3
"""Build deterministic, independently installable EditRoast skill archives."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


VERSION = "2.0.0-rc.1"
SKILLS = (
    "manuscript-roast",
    "manuscript-proofreading",
    "literary-translation",
    "book-production",
)
PACKS = {name: (name,) for name in SKILLS} | {"complete-pack": SKILLS}
ROOT_FILES = ("README.md", "LICENSE", "CHANGELOG.md", "install.py")
CORE_WRAPPERS = ("manuscript_workflow.py", "editroast_state.py", "editroast.py")
FIXED_ZIP_TIME = (2026, 10, 28, 0, 0, 0)


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.zip"),
    )


def stage_skill(repo: Path, skill_name: str, destination: Path) -> Path:
    """Create one self-contained skill tree from canonical repository sources."""
    if skill_name not in SKILLS:
        raise ValueError(f"Unknown skill: {skill_name}")
    source = repo / "skills" / skill_name
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"Missing skill source: {source}")

    target = destination / skill_name
    copy_tree(source, target)
    scripts = target / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for wrapper in CORE_WRAPPERS:
        shutil.copy2(repo / "scripts" / wrapper, scripts / wrapper)
    copy_tree(repo / "src" / "editroast_core", scripts / "editroast_core")
    copy_tree(repo / "templates", target / "templates")
    return target


def stage_pack(repo: Path, staging_root: Path, skills: tuple[str, ...]) -> None:
    for name in ROOT_FILES:
        shutil.copy2(repo / name, staging_root / name)
    (staging_root / "skills").mkdir()
    for skill in skills:
        stage_skill(repo, skill, staging_root / "skills")
    copy_tree(repo / "docs", staging_root / "docs")
    copy_tree(repo / "examples", staging_root / "examples")


def write_deterministic_zip(source_root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(source_root.parent).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def archive_path_errors(names: list[str]) -> list[str]:
    errors: list[str] = []
    for name in names:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or (path.parts and ":" in path.parts[0])
        ):
            errors.append(name)
    return errors


def verify_archive(path: Path, expected_skills: tuple[str, ...]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        unsafe = archive_path_errors(names)
        if unsafe:
            raise ValueError(f"{path.name}: unsafe archive paths {unsafe[:5]}")
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError(f"{path.name}: expected one top-level folder")
        root = next(iter(roots))
        required = {
            f"{root}/README.md",
            f"{root}/LICENSE",
            f"{root}/install.py",
        }
        for skill in expected_skills:
            base = f"{root}/skills/{skill}"
            required.update(
                {
                    f"{base}/SKILL.md",
                    f"{base}/scripts/manuscript_workflow.py",
                    f"{base}/scripts/editroast_state.py",
                    f"{base}/scripts/editroast.py",
                    f"{base}/scripts/editroast_core/__init__.py",
                    f"{base}/scripts/editroast_core/__main__.py",
                    f"{base}/scripts/editroast_core/cli.py",
                    f"{base}/scripts/editroast_core/manuscript_state.py",
                    f"{base}/scripts/editroast_core/manuscript_workflow.py",
                    f"{base}/scripts/editroast_core/revision_diff.py",
                    f"{base}/scripts/editroast_core/safe_apply.py",
                    f"{base}/scripts/editroast_core/editroast_state.py",
                    f"{base}/templates/book-map.md",
                }
            )
        missing = required - set(names)
        if missing:
            raise ValueError(f"{path.name}: missing {sorted(missing)}")
        forbidden = ("Authors/", "legacy/", "node_modules/", ".env", "send_report.py")
        bad = [name for name in names if any(marker in name for marker in forbidden)]
        if bad:
            raise ValueError(f"{path.name}: forbidden content {bad[:5]}")


def build_pack(repo: Path, dist: Path, pack_name: str, skills: tuple[str, ...]) -> Path:
    with tempfile.TemporaryDirectory(prefix="editroast-release-") as temporary:
        staging_root = Path(temporary) / f"editroast-{pack_name}-v{VERSION}"
        staging_root.mkdir()
        stage_pack(repo, staging_root, skills)
        archive = dist / f"editroast-{pack_name}-v{VERSION}.zip"
        write_deterministic_zip(staging_root, archive)
    verify_archive(archive, skills)
    return archive


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_all(repo: Path, dist: Path) -> list[Path]:
    dist.mkdir(parents=True, exist_ok=True)
    archives = [build_pack(repo, dist, name, skills) for name, skills in PACKS.items()]
    manifest = "".join(f"{sha256(path)}  {path.name}\n" for path in archives)
    (dist / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8", newline="\n")
    return archives


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    dist = (args.dist or repo / "dist").resolve()
    for archive in build_all(repo, dist):
        print(f"Built {archive} ({sha256(archive)})")
    print(f"Checksums: {dist / 'SHA256SUMS.txt'}")


if __name__ == "__main__":
    main()

