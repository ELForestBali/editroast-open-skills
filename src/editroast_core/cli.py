"""Command-line interface for durable EditRoast manuscript projects."""

from __future__ import annotations

import argparse
import json
import sqlite3

from .manuscript_state import ManuscriptProject
from .editorial_board import (
    preview_editorial_board, start_editorial_lenses, editorial_lenses_status,
    claim_editorial_lens_unit, complete_editorial_lens_unit,
    preview_completed_editorial_lenses,
)
from .book_map import (
    MAX_QUALITY_BYTES,
    advance_book_map,
    book_map_status,
    claim_book_map_unit,
    complete_book_map_unit,
    finalize_book_map,
    inventory_book,
    load_book_map,
    read_book_map,
    record_quality_assessment,
    start_book_map,
    validate_book_map,
)
from .project_bundle import (
    export_project_bundle,
    import_project_bundle,
    inspect_project_bundle,
)


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
    correction_add = manuscript_actions.add_parser("correction-add")
    correction_add.add_argument("project")
    correction_add.add_argument("revision")
    correction_add.add_argument("correction")
    correction_add.add_argument("--paragraph", required=True)
    correction_add.add_argument("--before", required=True)
    correction_add.add_argument("--after", required=True)
    correction_add.add_argument("--class", dest="change_class", choices=("mechanical", "semantic"), required=True)
    correction_add.add_argument("--start", type=int)
    correction_add.add_argument("--end", type=int)
    correction_add.add_argument("--finding")
    correction_add.add_argument("--correction-group")
    correction_add.add_argument("--category")
    correction_add.add_argument("--rationale")
    correction_add.add_argument("--actor", default="editor")
    correction_list = manuscript_actions.add_parser("correction-list")
    correction_list.add_argument("project")
    correction_list.add_argument("--revision")
    correction_decide = manuscript_actions.add_parser("correction-decide")
    correction_decide.add_argument("project")
    correction_decide.add_argument(
        "decision", choices=("accept", "approved", "rejected", "deferred", "pending")
    )
    correction_scope = correction_decide.add_mutually_exclusive_group(required=True)
    correction_scope.add_argument("--id", action="append", dest="correction_ids")
    correction_scope.add_argument("--correction-group")
    correction_scope.add_argument("--all-mechanical", action="store_true")
    correction_decide.add_argument("--actor", default="author")
    correction_decide.add_argument("--rationale")
    correction_decide.add_argument("--revision")
    correction_decide.add_argument("--confirm-author-approval", action="store_true")
    correction_decide.add_argument("--confirm-semantic-group", action="store_true")
    apply_preview = manuscript_actions.add_parser("apply-preview")
    apply_preview.add_argument("project")
    apply_preview.add_argument("revision")
    apply_preview.add_argument("--id", action="append", dest="correction_ids")
    apply_command = manuscript_actions.add_parser("apply")
    apply_command.add_argument("project")
    apply_command.add_argument("revision")
    apply_command.add_argument("--plan-hash", required=True)
    apply_command.add_argument("--id", action="append", dest="correction_ids")
    apply_command.add_argument("--actor", default="author")
    apply_command.add_argument("--confirm-author-approval", action="store_true")
    apply_history = manuscript_actions.add_parser("apply-history")
    apply_history.add_argument("project")
    rollback_preview = manuscript_actions.add_parser("rollback-preview")
    rollback_preview.add_argument("project")
    rollback_preview.add_argument("application")
    rollback = manuscript_actions.add_parser("rollback")
    rollback.add_argument("project")
    rollback.add_argument("application")
    rollback.add_argument("--plan-hash", required=True)
    rollback.add_argument("--actor", default="author")
    rollback.add_argument("--confirm-author-approval", action="store_true")

    run_group = groups.add_parser("run")
    run_actions = run_group.add_subparsers(dest="action", required=True)
    run_create = run_actions.add_parser("create")
    run_create.add_argument("project")
    run_create.add_argument("run_id")
    run_create.add_argument("revision")
    run_create.add_argument("--type", dest="run_type", required=True)
    run_create.add_argument("--scope", choices=("chapter", "paragraph"), default="chapter")
    run_create.add_argument("--actor", default="agent")
    for action in ("status", "resume", "verify", "history"):
        command = run_actions.add_parser(action)
        command.add_argument("project")
        command.add_argument("run_id")
        if action == "resume":
            command.add_argument("--actor", default="agent")
    run_claim = run_actions.add_parser("claim")
    run_claim.add_argument("project")
    run_claim.add_argument("run_id")
    run_claim.add_argument("--worker", required=True)
    run_claim.add_argument("--actor", default="agent")
    run_complete = run_actions.add_parser("complete")
    run_complete.add_argument("project")
    run_complete.add_argument("run_id")
    run_complete.add_argument("unit_id")
    run_complete.add_argument("--attempt", required=True)
    run_complete.add_argument("--result", required=True)
    run_complete.add_argument("--actor", default="agent")
    run_fail = run_actions.add_parser("fail")
    run_fail.add_argument("project")
    run_fail.add_argument("run_id")
    run_fail.add_argument("unit_id")
    run_fail.add_argument("--attempt", required=True)
    run_fail.add_argument("--error", required=True)
    run_fail.add_argument("--actor", default="agent")
    run_checkpoint = run_actions.add_parser("checkpoint")
    run_checkpoint.add_argument("project")
    run_checkpoint.add_argument("run_id")
    run_checkpoint.add_argument(
        "--status",
        required=True,
        choices=("paused", "blocked_by_usage_limit", "needs_author_decision"),
    )
    run_checkpoint.add_argument("--reason")
    run_checkpoint.add_argument("--actor", default="agent")

    book_map = groups.add_parser("book-map")
    book_map_actions = book_map.add_subparsers(dest="action", required=True)
    book_inventory = book_map_actions.add_parser("inventory")
    book_inventory.add_argument("project")
    book_inventory.add_argument("revision")
    book_inventory.add_argument("--include-text", action="store_true")
    book_validate = book_map_actions.add_parser("validate")
    book_validate.add_argument("project")
    book_validate.add_argument("revision")
    book_validate.add_argument("--map", dest="map_file", required=True)
    book_start = book_map_actions.add_parser("start")
    book_start.add_argument("project")
    book_start.add_argument("map_run_id")
    book_start.add_argument("revision")
    book_start.add_argument("--actor", default="agent")
    book_status = book_map_actions.add_parser("status")
    book_status.add_argument("project")
    book_status.add_argument("map_run_id")
    book_advance = book_map_actions.add_parser("advance")
    book_advance.add_argument("project")
    book_advance.add_argument("map_run_id")
    book_advance.add_argument("--actor", default="agent")
    book_claim = book_map_actions.add_parser("claim")
    book_claim.add_argument("project")
    book_claim.add_argument("map_run_id")
    book_claim.add_argument("--pass", dest="pass_name", choices=("A", "B", "a", "b"), required=True)
    book_claim.add_argument("--worker", required=True)
    book_claim.add_argument("--actor", default="agent")
    book_claim.add_argument("--include-text", action="store_true")
    book_complete = book_map_actions.add_parser("complete")
    book_complete.add_argument("project")
    book_complete.add_argument("map_run_id")
    book_complete.add_argument("unit_id")
    book_complete.add_argument("--pass", dest="pass_name", choices=("A", "B", "a", "b"), required=True)
    book_complete.add_argument("--attempt", required=True)
    book_complete.add_argument("--result", required=True)
    book_complete.add_argument("--actor", default="agent")
    book_finalize = book_map_actions.add_parser("finalize")
    book_finalize.add_argument("project")
    book_finalize.add_argument("map_run_id")
    book_show = book_map_actions.add_parser("show")
    book_show.add_argument("project")
    book_show.add_argument("map_run_id")
    book_quality = book_map_actions.add_parser("quality-record")
    book_quality.add_argument("project")
    book_quality.add_argument("map_run_id")
    book_quality.add_argument("--assessment", required=True)
    book_quality.add_argument("--actor", default="reviewer")

    editorial_group = groups.add_parser("editorial")
    editorial_actions = editorial_group.add_subparsers(dest="action", required=True)
    editorial_preview = editorial_actions.add_parser("board-preview")
    editorial_preview.add_argument("project")
    editorial_preview.add_argument("map_run_id")
    editorial_preview.add_argument("--reviews", required=True)
    editorial_start = editorial_actions.add_parser("start")
    editorial_start.add_argument("project")
    editorial_start.add_argument("map_run_id")
    editorial_start.add_argument("review_id")
    editorial_status = editorial_actions.add_parser("status")
    editorial_status.add_argument("project")
    editorial_status.add_argument("map_run_id")
    editorial_status.add_argument("review_id")
    editorial_claim = editorial_actions.add_parser("claim")
    editorial_claim.add_argument("project")
    editorial_claim.add_argument("map_run_id")
    editorial_claim.add_argument("review_id")
    editorial_claim.add_argument("--persona", required=True)
    editorial_claim.add_argument("--worker", required=True)
    editorial_claim.add_argument("--include-text", action="store_true")
    editorial_complete = editorial_actions.add_parser("complete")
    editorial_complete.add_argument("project")
    editorial_complete.add_argument("map_run_id")
    editorial_complete.add_argument("review_id")
    editorial_complete.add_argument("unit_id")
    editorial_complete.add_argument("--persona", required=True)
    editorial_complete.add_argument("--attempt", required=True)
    editorial_complete.add_argument("--result", required=True)
    editorial_result = editorial_actions.add_parser("preview")
    editorial_result.add_argument("project")
    editorial_result.add_argument("map_run_id")
    editorial_result.add_argument("review_id")

    bundle_group = groups.add_parser("bundle")
    bundle_actions = bundle_group.add_subparsers(dest="action", required=True)
    bundle_export = bundle_actions.add_parser("export")
    bundle_export.add_argument("project")
    bundle_export.add_argument("output")
    bundle_inspect = bundle_actions.add_parser("inspect")
    bundle_inspect.add_argument("bundle")
    bundle_import = bundle_actions.add_parser("import")
    bundle_import.add_argument("bundle")
    bundle_import.add_argument("destination")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.group == "bundle" and args.action == "inspect":
        payload = inspect_project_bundle(args.bundle)
    elif args.group == "bundle" and args.action == "import":
        payload = import_project_bundle(args.bundle, args.destination)
    elif args.group == "bundle" and args.action == "export":
        with ManuscriptProject.open(args.project) as project:
            payload = export_project_bundle(project, args.output)
    elif args.group == "project" and args.action == "create":
        with ManuscriptProject.create(
            args.path, title=args.title, author=args.author, language=args.language
        ) as project:
            payload = project.status()
    else:
        with ManuscriptProject.open(args.project) as project:
            if args.group == "editorial" and args.action == "board-preview":
                payload = preview_editorial_board(
                    project, args.map_run_id, read_book_map(args.reviews)
                )
            elif args.group == "editorial" and args.action == "start":
                payload = start_editorial_lenses(project, args.map_run_id, args.review_id)
            elif args.group == "editorial" and args.action == "status":
                payload = editorial_lenses_status(project, args.map_run_id, args.review_id)
            elif args.group == "editorial" and args.action == "claim":
                payload = claim_editorial_lens_unit(
                    project, args.map_run_id, args.review_id, args.persona,
                    worker_id=args.worker, include_text=args.include_text,
                )
            elif args.group == "editorial" and args.action == "complete":
                payload = complete_editorial_lens_unit(
                    project, args.map_run_id, args.review_id, args.persona,
                    args.unit_id, attempt_uid=args.attempt, result=args.result,
                )
            elif args.group == "editorial" and args.action == "preview":
                payload = preview_completed_editorial_lenses(
                    project, args.map_run_id, args.review_id,
                )
            elif args.group == "book-map" and args.action == "inventory":
                payload = inventory_book(project, args.revision, include_text=args.include_text)
            elif args.group == "book-map" and args.action == "validate":
                payload = validate_book_map(project, args.revision, read_book_map(args.map_file))
            elif args.group == "book-map" and args.action == "start":
                payload = start_book_map(
                    project, args.revision, args.map_run_id, actor=args.actor
                )
            elif args.group == "book-map" and args.action == "status":
                payload = book_map_status(project, args.map_run_id)
            elif args.group == "book-map" and args.action == "advance":
                payload = advance_book_map(project, args.map_run_id, actor=args.actor)
            elif args.group == "book-map" and args.action == "claim":
                payload = claim_book_map_unit(
                    project,
                    args.map_run_id,
                    args.pass_name,
                    worker_id=args.worker,
                    actor=args.actor,
                    include_text=args.include_text,
                )
            elif args.group == "book-map" and args.action == "complete":
                payload = complete_book_map_unit(
                    project,
                    args.map_run_id,
                    args.pass_name,
                    args.unit_id,
                    attempt_uid=args.attempt,
                    result=args.result,
                    actor=args.actor,
                )
            elif args.group == "book-map" and args.action == "finalize":
                payload = finalize_book_map(project, args.map_run_id)
            elif args.group == "book-map" and args.action == "show":
                payload = load_book_map(project, args.map_run_id)
            elif args.group == "book-map" and args.action == "quality-record":
                payload = record_quality_assessment(
                    project,
                    args.map_run_id,
                    read_book_map(args.assessment, max_bytes=MAX_QUALITY_BYTES),
                    actor=args.actor,
                )
            elif args.group == "run" and args.action == "create":
                payload = project.create_run(
                    args.run_id,
                    args.revision,
                    run_type=args.run_type,
                    scope=args.scope,
                    actor=args.actor,
                )
            elif args.group == "run" and args.action == "status":
                payload = project.run_status(args.run_id)
            elif args.group == "run" and args.action == "claim":
                payload = project.claim_run_unit(
                    args.run_id, worker_id=args.worker, actor=args.actor
                )
            elif args.group == "run" and args.action == "complete":
                payload = project.complete_run_unit(
                    args.run_id,
                    args.unit_id,
                    attempt_uid=args.attempt,
                    result=args.result,
                    actor=args.actor,
                )
            elif args.group == "run" and args.action == "fail":
                payload = project.fail_run_unit(
                    args.run_id,
                    args.unit_id,
                    attempt_uid=args.attempt,
                    error=args.error,
                    actor=args.actor,
                )
            elif args.group == "run" and args.action == "checkpoint":
                payload = project.checkpoint_run(
                    args.run_id, args.status, actor=args.actor, reason=args.reason
                )
            elif args.group == "run" and args.action == "resume":
                payload = project.resume_run(args.run_id, actor=args.actor)
            elif args.group == "run" and args.action == "verify":
                payload = project.verify_run(args.run_id)
            elif args.group == "run" and args.action == "history":
                payload = project.run_history(args.run_id)
            elif args.action == "import":
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
            elif args.action == "correction-add":
                payload = project.add_correction(
                    args.correction,
                    args.revision,
                    paragraph_stable_id=args.paragraph,
                    exact_before=args.before,
                    replacement=args.after,
                    change_class=args.change_class,
                    start_offset=args.start,
                    end_offset=args.end,
                    finding_id=args.finding,
                    group_uid=args.correction_group,
                    category=args.category,
                    rationale=args.rationale,
                    actor=args.actor,
                )
            elif args.action == "correction-list":
                payload = project.corrections(args.revision)
            elif args.action == "correction-decide":
                payload = project.decide_corrections(
                    "approved" if args.decision == "accept" else args.decision,
                    correction_ids=args.correction_ids,
                    group_uid=args.correction_group,
                    all_mechanical=args.all_mechanical,
                    revision_uid=args.revision,
                    actor=args.actor,
                    rationale=args.rationale,
                    author_confirmed=args.confirm_author_approval,
                    confirm_semantic_group=args.confirm_semantic_group,
                )
            elif args.action == "apply-preview":
                payload = project.preview_correction_apply(
                    args.revision, correction_ids=args.correction_ids
                )
            elif args.action == "apply":
                payload = project.apply_corrections(
                    args.revision,
                    plan_hash=args.plan_hash,
                    correction_ids=args.correction_ids,
                    actor=args.actor,
                    author_confirmed=args.confirm_author_approval,
                )
            elif args.action == "apply-history":
                payload = project.application_history()
            elif args.action == "rollback-preview":
                payload = project.preview_rollback(args.application)
            elif args.action == "rollback":
                payload = project.rollback_application(
                    args.application,
                    plan_hash=args.plan_hash,
                    actor=args.actor,
                    author_confirmed=args.confirm_author_approval,
                )
            else:
                payload = project.verify()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    verification = (args.action == "verify" and args.group in {"manuscript", "run"}) or (args.group == "book-map" and args.action == "validate") or (args.group == "editorial" and args.action == "board-preview")
    return 2 if verification and not payload["ok"] else 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}")
        return 1
