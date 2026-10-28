"""Command-line interface for durable EditRoast manuscript projects."""

from __future__ import annotations

import argparse
import json
import sqlite3

from .manuscript_state import ManuscriptProject


def _metadata(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--language")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="editroast", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True)

    project = groups.add_parser("project")
    project_actions = project.add_subparsers(dest="action", required=True)
    create = project_actions.add_parser("create")
    create.add_argument("path")
    _metadata(create)

    manuscript = groups.add_parser("manuscript")
    manuscript_actions = manuscript.add_subparsers(dest="action", required=True)
    import_command = manuscript_actions.add_parser("import")
    import_command.add_argument("project")
    import_command.add_argument("source")
    _metadata(import_command)
    for action in ("status", "revisions", "verify"):
        command = manuscript_actions.add_parser(action)
        command.add_argument("project")
    show = manuscript_actions.add_parser("show")
    show.add_argument("project")
    show.add_argument("revision")
    diff = manuscript_actions.add_parser("diff")
    diff.add_argument("project")
    diff.add_argument("revision", help="Target revision; compared with its direct parent")
    diff.add_argument("--from", dest="from_revision")
    diff.add_argument("--include-text", action="store_true")
    reconcile_list = manuscript_actions.add_parser("reconcile-list")
    reconcile_list.add_argument("project")
    reconcile_list.add_argument("revision")
    reconcile_list.add_argument("--include-text", action="store_true")
    reconcile = manuscript_actions.add_parser("reconcile")
    reconcile.add_argument("project")
    reconcile.add_argument("revision")
    reconcile.add_argument("group")
    choice = reconcile.add_mutually_exclusive_group(required=True)
    choice.add_argument("--candidate")
    choice.add_argument(
        "--relation",
        choices=("unchanged", "changed", "moved", "split", "merged", "added", "removed"),
    )
    reconcile.add_argument("--before", action="append", default=[])
    reconcile.add_argument("--after", action="append", default=[])
    reconcile.add_argument("--actor", default="author")
    reconcile.add_argument("--note")
    reopen = manuscript_actions.add_parser("reconcile-reopen")
    reopen.add_argument("project")
    reopen.add_argument("revision")
    reopen.add_argument("group")
    reopen.add_argument("--actor", default="author")
    reopen.add_argument("--note")
    history = manuscript_actions.add_parser("reconcile-history")
    history.add_argument("project")
    history.add_argument("revision")
    history.add_argument("--group")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.group == "project" and args.action == "create":
        with ManuscriptProject.create(
            args.path, title=args.title, author=args.author, language=args.language
        ) as project:
            payload = project.status()
    else:
        with ManuscriptProject.open(args.project) as project:
            if args.action == "import":
                payload = project.import_manuscript(
                    args.source, title=args.title, author=args.author, language=args.language
                )
            elif args.action == "status":
                payload = project.status()
            elif args.action == "revisions":
                payload = project.revisions()
            elif args.action == "show":
                payload = project.revision(args.revision)
            elif args.action == "diff":
                payload = project.revision_diff(
                    args.revision,
                    from_revision=args.from_revision,
                    include_text=args.include_text,
                )
            elif args.action == "reconcile-list":
                payload = project.reconciliation_queue(
                    args.revision, include_text=args.include_text
                )
            elif args.action == "reconcile":
                payload = project.reconcile(
                    args.revision,
                    args.group,
                    relation_type=args.relation,
                    before_ids=args.before,
                    after_ids=args.after,
                    candidate_uid=args.candidate,
                    actor=args.actor,
                    note=args.note,
                )
            elif args.action == "reconcile-reopen":
                payload = project.reopen_reconciliation(
                    args.revision, args.group, actor=args.actor, note=args.note
                )
            elif args.action == "reconcile-history":
                payload = project.reconciliation_history(args.revision, args.group)
            else:
                payload = project.verify()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if args.group == "manuscript" and args.action == "verify" and not payload["ok"] else 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}")
        return 1
