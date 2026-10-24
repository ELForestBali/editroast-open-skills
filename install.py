#!/usr/bin/env python3
"""Install EditRoast skills into a Codex or Claude Code skill directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


SKILLS = {
    "roast": "manuscript-roast",
    "proofreading": "manuscript-proofreading",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("codex", "claude"), required=True)
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--skill", choices=("roast", "proofreading", "all"), default="all")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--force", action="store_true", help="Replace an existing installed skill")
    return parser.parse_args()


def destination_root(args: argparse.Namespace) -> Path:
    if args.scope == "user":
        return Path.home() / (".agents/skills" if args.target == "codex" else ".claude/skills")
    return args.project_dir.resolve() / (".agents/skills" if args.target == "codex" else ".claude/skills")


def install_one(repo: Path, destination: Path, skill_name: str, force: bool) -> None:
    source = repo / "skills" / skill_name
    target = destination / skill_name
    if target.exists():
        if not force:
            raise SystemExit(f"Refusing to replace existing skill: {target}. Re-run with --force if intended.")
        shutil.rmtree(target)
    shutil.copytree(source, target)
    shutil.copytree(repo / "scripts", target / "scripts", ignore=shutil.ignore_patterns("__pycache__", "build_release.py"))
    shutil.copytree(repo / "templates", target / "templates")
    print(f"Installed {skill_name} -> {target}")


def main() -> None:
    args = parse_args()
    repo = Path(__file__).resolve().parent
    root = destination_root(args)
    root.mkdir(parents=True, exist_ok=True)
    available = {key: name for key, name in SKILLS.items() if (repo / "skills" / name).is_dir()}
    if args.skill == "all":
        selected = available.values()
    else:
        if args.skill not in available:
            raise SystemExit(f"This archive does not contain the {args.skill} skill")
        selected = (available[args.skill],)
    if not selected:
        raise SystemExit("No skills found in this archive")
    for name in selected:
        install_one(repo, root, name, args.force)
    print("Done. Restart the agent if the new skills do not appear immediately.")


if __name__ == "__main__":
    main()

