#!/usr/bin/env python3
"""Install self-contained EditRoast skills for Codex or Claude Code."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


SKILL_ALIASES = {
    "roast": "manuscript-roast",
    "proofreading": "manuscript-proofreading",
    "translation": "literary-translation",
    "production": "book-production",
    "manuscript-roast": "manuscript-roast",
    "manuscript-proofreading": "manuscript-proofreading",
    "literary-translation": "literary-translation",
    "book-production": "book-production",
}
SKILLS = tuple(dict.fromkeys(SKILL_ALIASES.values()))
REQUIRED_BUNDLE_FILES = (
    "SKILL.md",
    "scripts/manuscript_workflow.py",
    "scripts/editroast_state.py",
    "scripts/editroast_core/__init__.py",
    "templates/book-map.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("codex", "claude"), required=True)
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument(
        "--skill", choices=(*SKILL_ALIASES, "all"), default="all"
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--force", action="store_true", help="Replace an installed skill")
    return parser.parse_args()


def destination_root(args: argparse.Namespace) -> Path:
    folder = Path(".agents/skills") if args.target == "codex" else Path(".claude/skills")
    if args.scope == "user":
        return Path.home() / folder
    return args.project_dir.resolve() / folder


def selected_skills(repo: Path, value: str) -> tuple[str, ...]:
    available = tuple(name for name in SKILLS if (repo / "skills" / name / "SKILL.md").is_file())
    if value == "all":
        if not available:
            raise SystemExit("No skills found in this archive")
        return available
    selected = SKILL_ALIASES[value]
    if selected not in available:
        raise SystemExit(f"This archive does not contain the {selected} skill")
    return (selected,)


def is_self_contained(skill: Path) -> bool:
    return all((skill / relative).is_file() for relative in REQUIRED_BUNDLE_FILES)


def prepare_source(repo: Path, skill_name: str, staging: Path) -> Path:
    source = repo / "skills" / skill_name
    if is_self_contained(source):
        return source
    try:
        from scripts.build_release import stage_skill
    except ImportError as error:
        raise SystemExit(
            f"{skill_name} is not bundled and the source bundler is unavailable"
        ) from error
    return stage_skill(repo, skill_name, staging)


def install_one(source: Path, destination: Path, force: bool) -> None:
    target = destination / source.name
    if target.exists():
        if not force:
            raise SystemExit(
                f"Refusing to replace existing skill: {target}. Re-run with --force if intended."
            )
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    missing = [relative for relative in REQUIRED_BUNDLE_FILES if not (target / relative).is_file()]
    if missing:
        raise SystemExit(f"Incomplete installation for {source.name}: missing {missing}")
    print(f"Installed {source.name} -> {target}")


def main() -> None:
    args = parse_args()
    repo = Path(__file__).resolve().parent
    destination = destination_root(args)
    destination.mkdir(parents=True, exist_ok=True)
    names = selected_skills(repo, args.skill)

    for name in names:
        target = destination / name
        if target.exists() and not args.force:
            raise SystemExit(
                f"Refusing to replace existing skill: {target}. Re-run with --force if intended."
            )

    with tempfile.TemporaryDirectory(prefix="editroast-install-") as temporary:
        staging = Path(temporary)
        for name in names:
            source = prepare_source(repo, name, staging)
            install_one(source, destination, args.force)
    print("Done. Restart the agent if the new skills do not appear immediately.")


if __name__ == "__main__":
    main()

