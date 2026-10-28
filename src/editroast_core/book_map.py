"""Revision-bound, two-pass whole-book mapping for ER-020.

The offline core validates structure, resumable coverage and source evidence. It
does not generate literary judgments or claim that a valid map is a good one.
Manuscript content and submitted records are untrusted data, never commands.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .manuscript_state import (
    ManuscriptProject,
    _safe_project_relative,
    sha256_file,
    utc_now,
)

MAP_SCHEMA_VERSION = 2
MAX_MAP_BYTES = 8 * 1024 * 1024
MAX_QUALITY_BYTES = 1024 * 1024
RECORD_KINDS = frozenset({
    "character", "alias", "relationship", "plotline", "scene", "narrator_pov",
    "goal", "change", "event", "timeline_anchor", "causal_link", "promise",
    "payoff", "motif", "symbol", "authorial_device", "story_rule",
    "genre_expectation", "premise", "narrative_structure", "open_question",
    "disagreement",
})
RECORD_CLASSES = frozenset({"observed_fact", "editorial_hypothesis", "author_decision"})
EPISTEMIC_LABELS = frozenset({"observed", "inferred", "uncertain", "author_confirmed"})
NARRATIVE_FRAMES = frozenset({
    "literal", "dream", "imagined", "recollection", "flashback", "flashforward",
    "unreliable_narration", "ambiguous",
})
SCOPES = frozenset({"chapter", "scene", "cross_book"})
REVIEW_STATUSES = frozenset({"provisional", "confirmed", "disputed", "open", "superseded"})
CHRONOLOGY_LABELS = frozenset({"exact", "relative", "unknown", "nonlinear", "ambiguous"})
POV_TYPES = frozenset({
    "first_person", "close_third", "omniscient", "second_person", "objective",
    "epistolary", "stream_of_consciousness", "multiple", "other", "ambiguous",
})
MAP_RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _source(project: ManuscriptProject, revision_uid: str):
    revision = project.connection.execute(
        "SELECT * FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
    ).fetchone()
    if revision is None:
        raise ValueError(f"Unknown revision: {revision_uid}")
    relative = _safe_project_relative(revision["snapshot_path"])
    if relative is None:
        raise ValueError("Unsafe manuscript snapshot path")
    source = (project.root / relative).resolve()
    if not source.is_relative_to(project.root.resolve()) or not source.is_file():
        raise ValueError("Manuscript snapshot is missing or outside the project")
    if source.stat().st_size != revision["source_size"] or sha256_file(source) != revision["source_sha256"]:
        raise ValueError("Manuscript snapshot integrity check failed")
    return revision


def _map_run_id(value: str) -> str:
    if not isinstance(value, str) or not MAP_RUN_RE.fullmatch(value):
        raise ValueError(
            "Map run ID must be 1-128 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def _pass_run_id(map_run_id: str, pass_name: str) -> str:
    return f"{_map_run_id(map_run_id)}.pass-{pass_name.lower()}"


def inventory_book(project: ManuscriptProject, revision_uid: str, *, include_text: bool = False) -> dict:
    """Return a deterministic inventory; manuscript text is opt-in."""
    revision = _source(project, revision_uid)
    project_row = project.connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if project_row is None:
        raise ValueError("Project identity is missing")
    chapters = [dict(row) for row in project.connection.execute(
        "SELECT stable_id,source_order,title,start_paragraph,end_paragraph "
        "FROM chapters WHERE revision_uid=? ORDER BY source_order", (revision_uid,)
    )]
    paragraph_rows = [dict(row) for row in project.connection.execute(
        "SELECT stable_id,chapter_stable_id,source_order,chapter_order,text "
        "FROM paragraphs WHERE revision_uid=? ORDER BY source_order", (revision_uid,)
    )]
    if len(chapters) != revision["chapter_count"] or len(paragraph_rows) != revision["paragraph_count"]:
        raise ValueError("Revision inventory is incomplete")
    chapter_ids = {row["stable_id"] for row in chapters}
    paragraph_ids = {row["stable_id"] for row in paragraph_rows}
    if len(chapter_ids) != len(chapters) or len(paragraph_ids) != len(paragraph_rows):
        raise ValueError("Revision inventory has duplicate stable IDs")
    if any(row["chapter_stable_id"] not in chapter_ids for row in paragraph_rows):
        raise ValueError("A paragraph refers to an unknown chapter")
    payload = []
    for chapter in chapters:
        items = []
        for paragraph in paragraph_rows:
            if paragraph["chapter_stable_id"] != chapter["stable_id"]:
                continue
            item = {
                "stable_id": paragraph["stable_id"],
                "source_order": paragraph["source_order"],
                "chapter_order": paragraph["chapter_order"],
            }
            if include_text:
                item["text"] = paragraph["text"]
            items.append(item)
        payload.append({
            "stable_id": chapter["stable_id"],
            "source_order": chapter["source_order"],
            "title": chapter["title"],
            "paragraphs": items,
        })
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "project_id": project_row["id"],
        "revision_uid": revision_uid,
        "source_sha256": revision["source_sha256"],
        "source_paragraph_count": len(paragraph_rows),
        "source_chapter_count": len(chapters),
        "chapters": payload,
        "records": [],
        "analysis_state": "not_started",
        "source_inventory_complete": True,
        "editorial_analysis_complete": False,
    }


def read_book_map(path: str | Path, *, max_bytes: int = MAX_MAP_BYTES) -> dict:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError(f"JSON file is missing or exceeds the {max_bytes}-byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid book map JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Book map must be a JSON object")
    return value


def _record_class(record: dict) -> str | None:
    value = record.get("record_class")
    if value is not None:
        return value
    return {
        "observed": "observed_fact",
        "inferred": "editorial_hypothesis",
        "uncertain": "editorial_hypothesis",
        "author_confirmed": "author_decision",
    }.get(record.get("epistemic_status"))


def _source_paragraphs(project: ManuscriptProject, revision_uid: str) -> dict[str, dict]:
    return {
        row["stable_id"]: dict(row)
        for row in project.connection.execute(
            "SELECT stable_id,chapter_stable_id,source_order,text FROM paragraphs "
            "WHERE revision_uid=?", (revision_uid,)
        )
    }


def _validate_records(
    project: ManuscriptProject,
    revision_uid: str,
    records: object,
    *,
    strict: bool,
    expected_chapter_id: str | None = None,
    expected_pass: str | None = None,
) -> tuple[list[str], int, set[str]]:
    errors: list[str] = []
    if not isinstance(records, list):
        return ["records: expected a list"], 0, set()
    paragraphs = _source_paragraphs(project, revision_uid)
    seen: set[str] = set()
    evidence_count = 0
    links: list[tuple[str, str]] = []
    for i, record in enumerate(records):
        prefix = f"records[{i}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        rid = record.get("id")
        if not isinstance(rid, str) or not rid.strip() or rid in seen:
            errors.append(f"{prefix}: missing or duplicate record ID")
        else:
            seen.add(rid)
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in RECORD_KINDS:
            errors.append(f"{prefix}: unsupported record kind")
        epistemic = record.get("epistemic_status")
        if epistemic not in EPISTEMIC_LABELS:
            errors.append(f"{prefix}: unsupported epistemic status")
        record_class = _record_class(record)
        if record_class not in RECORD_CLASSES or (strict and "record_class" not in record):
            errors.append(f"{prefix}: unsupported or missing record class")
        expected_epistemic = {
            "observed_fact": {"observed"},
            "editorial_hypothesis": {"inferred", "uncertain"},
            "author_decision": {"author_confirmed"},
        }.get(record_class, set())
        if epistemic in EPISTEMIC_LABELS and epistemic not in expected_epistemic:
            errors.append(f"{prefix}: record class conflicts with epistemic status")
        scope = record.get("scope")
        if not isinstance(scope, str) or scope not in SCOPES:
            errors.append(f"{prefix}: unsupported scope")
        narrative_frame = record.get("narrative_frame")
        if not isinstance(narrative_frame, str) or narrative_frame not in NARRATIVE_FRAMES:
            errors.append(f"{prefix}: unsupported narrative frame")
        if not isinstance(record.get("statement"), str) or not record["statement"].strip():
            errors.append(f"{prefix}: missing statement")
        review_status = record.get("review_status")
        if strict:
            if review_status not in REVIEW_STATUSES:
                errors.append(f"{prefix}: unsupported or missing review status")
            elif expected_pass == "A" and review_status != "provisional":
                errors.append(f"{prefix}: Pass A records must remain provisional")
            elif expected_pass == "B" and review_status == "provisional":
                errors.append(f"{prefix}: Pass B records must carry a reviewed status")
        pov_type = record.get("pov_type")
        if pov_type is not None and pov_type not in POV_TYPES:
            errors.append(f"{prefix}: unsupported POV type")
        sequence = record.get("sequence")
        if sequence is not None:
            if not isinstance(sequence, dict):
                errors.append(f"{prefix}: sequence must be an object")
            else:
                order = sequence.get("reading_order")
                if not isinstance(order, int) or isinstance(order, bool) or order < 0:
                    errors.append(f"{prefix}: sequence reading_order must be a non-negative integer")
                if sequence.get("chronology") not in CHRONOLOGY_LABELS:
                    errors.append(f"{prefix}: unsupported chronology label")
                story_time = sequence.get("story_time")
                if story_time is not None and not isinstance(story_time, str):
                    errors.append(f"{prefix}: story_time must be text or null")
        elif strict and record.get("kind") in {"scene", "event", "timeline_anchor"}:
            errors.append(f"{prefix}: scene/event/timeline records require sequence metadata")
        record_links = record.get("links", [])
        if not isinstance(record_links, list):
            errors.append(f"{prefix}: links must be a list")
        else:
            for j, link in enumerate(record_links):
                if (
                    not isinstance(link, dict)
                    or not isinstance(link.get("relation"), str)
                    or not link["relation"].strip()
                    or not isinstance(link.get("target_id"), str)
                    or not link["target_id"].strip()
                ):
                    errors.append(f"{prefix}.links[{j}]: malformed record link")
                elif isinstance(rid, str):
                    links.append((rid, link["target_id"]))
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}: at least one evidence anchor is required")
            continue
        local_anchor = expected_chapter_id is None
        for j, anchor in enumerate(evidence):
            ap = f"{prefix}.evidence[{j}]"
            if not isinstance(anchor, dict):
                errors.append(f"{ap}: malformed evidence")
                continue
            if anchor.get("revision_uid") != revision_uid:
                errors.append(f"{ap}: revision mismatch")
            paragraph_id = anchor.get("paragraph_stable_id")
            paragraph = paragraphs.get(paragraph_id) if isinstance(paragraph_id, str) else None
            quote = anchor.get("exact_quote")
            if paragraph is None:
                errors.append(f"{ap}: unknown paragraph stable ID")
            elif paragraph["chapter_stable_id"] == expected_chapter_id:
                local_anchor = True
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{ap}: empty quotation")
                continue
            if paragraph is None:
                continue
            start = anchor.get("start_offset")
            end = anchor.get("end_offset")
            if (start is None) != (end is None):
                errors.append(f"{ap}: offsets must be supplied together")
            elif start is not None:
                if (
                    not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or start < 0 or end < start or end > len(paragraph["text"])
                ):
                    errors.append(f"{ap}: invalid quotation offsets")
                elif paragraph["text"][start:end] != quote:
                    errors.append(f"{ap}: quotation offsets do not match source")
                else:
                    evidence_count += 1
            elif quote not in paragraph["text"]:
                errors.append(f"{ap}: quoted text does not match source")
            else:
                evidence_count += 1
        if not local_anchor:
            errors.append(f"{prefix}: no evidence anchor belongs to the claimed chapter")
    if not strict:
        for source_id, target_id in links:
            if target_id not in seen:
                errors.append(f"record {source_id}: link target does not exist: {target_id}")
    return errors, evidence_count, seen


def validate_book_map(project: ManuscriptProject, revision_uid: str, payload: dict) -> dict:
    """Verify provenance, inventory and evidence; do not certify literary quality."""
    expected = inventory_book(project, revision_uid, include_text=False)
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise ValueError("Book map must be a JSON object")
    if payload.get("schema_version") not in {1, MAP_SCHEMA_VERSION}:
        errors.append("schema_version: unsupported book-map schema")
    for name in ("project_id", "revision_uid", "source_sha256"):
        if payload.get(name) != expected[name]:
            errors.append(f"{name}: value does not match the immutable revision")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        errors.append("chapters: expected a list")
        chapters = []
    expected_chapters = {c["stable_id"]: c for c in expected["chapters"]}
    if len(chapters) != len(expected_chapters):
        errors.append("chapters: whole-book chapter coverage mismatch")
    seen_chapters: set[str] = set()
    paragraph_text = {key: value["text"] for key, value in _source_paragraphs(project, revision_uid).items()}
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get("stable_id"), str):
            errors.append("chapters: malformed chapter")
            continue
        cid = chapter["stable_id"]
        if cid in seen_chapters:
            errors.append(f"chapters: duplicate chapter {cid}")
        seen_chapters.add(cid)
        original = expected_chapters.get(cid)
        if original is None:
            errors.append(f"chapters: unknown chapter {cid}")
            continue
        if chapter.get("title") != original["title"] or chapter.get("source_order") != original["source_order"]:
            errors.append(f"chapters: source heading/order mismatch for {cid}")
        refs = chapter.get("paragraphs")
        if not isinstance(refs, list):
            errors.append(f"chapters: missing paragraph inventory for {cid}")
            continue
        got = [item.get("stable_id") if isinstance(item, dict) else None for item in refs]
        want = [item["stable_id"] for item in original["paragraphs"]]
        if got != want:
            errors.append(f"chapters: paragraph coverage/order mismatch for {cid}")
        for actual, original_paragraph in zip(refs, original["paragraphs"]):
            if isinstance(actual, dict) and actual.get("source_order") != original_paragraph["source_order"]:
                errors.append(f"chapters: paragraph source order mismatch for {cid}")
            if isinstance(actual, dict) and "text" in actual:
                if actual["text"] != paragraph_text.get(actual.get("stable_id")):
                    errors.append("chapters: source paragraph text mismatch")
    if seen_chapters != set(expected_chapters):
        errors.append("chapters: missing or additional chapter IDs")
    record_errors, evidence_count, _ = _validate_records(
        project, revision_uid, payload.get("records"), strict=False
    )
    errors.extend(record_errors)
    return {
        "ok": not errors,
        "errors": errors,
        "revision_uid": revision_uid,
        "source_inventory_complete": not any(error.startswith("chapters:") for error in errors),
        "source_evidence_verified": not any(
            "evidence" in error or "quote" in error or "quoted" in error
            or "quotation" in error or "revision mismatch" in error
            for error in errors
        ),
        "data_integrity_assessed": True,
        "editorial_analysis_complete": bool(payload.get("editorial_analysis_complete")) and not errors,
        "editorial_quality_assessed": payload.get("editorial_quality", {}).get("status") == "assessed",
        "records_checked": len(payload.get("records", [])) if isinstance(payload.get("records"), list) else 0,
        "evidence_checked": evidence_count,
    }


def _run_exists(project: ManuscriptProject, run_id: str) -> bool:
    return project.connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is not None


def start_book_map(
    project: ManuscriptProject,
    revision_uid: str,
    map_run_id: str,
    *,
    actor: str = "agent",
) -> dict:
    """Create Pass A using the ER-014 resumable engine."""
    map_run_id = _map_run_id(map_run_id)
    _source(project, revision_uid)
    if project.connection.execute(
        "SELECT 1 FROM book_maps WHERE map_run_id=?", (map_run_id,)
    ).fetchone():
        raise ValueError(f"Book map {map_run_id} is already finalized")
    project.create_run(
        _pass_run_id(map_run_id, "A"),
        revision_uid,
        run_type="book-map-pass-a",
        scope="chapter",
        actor=actor,
        provenance={"book_map_run_id": map_run_id, "book_map_pass": "A"},
    )
    return book_map_status(project, map_run_id)


def _load_run_results(project: ManuscriptProject, run_id: str) -> list[tuple[dict, dict]]:
    status = project.run_status(run_id)
    results: list[tuple[dict, dict]] = []
    for unit in status["units"]:
        if not unit["verified"]:
            continue
        relative = _safe_project_relative(unit["result_path"])
        if relative is None:
            raise ValueError(f"Unsafe result path for {run_id}:{unit['unit_id']}")
        payload = read_book_map(project.root / relative)
        results.append((unit, payload))
    return results


def book_map_status(project: ManuscriptProject, map_run_id: str) -> dict:
    map_run_id = _map_run_id(map_run_id)
    pass_a_id = _pass_run_id(map_run_id, "A")
    if not _run_exists(project, pass_a_id):
        raise ValueError(f"Unknown book-map run: {map_run_id}")
    pass_a = project.run_status(pass_a_id)
    pass_b_id = _pass_run_id(map_run_id, "B")
    pass_b = project.run_status(pass_b_id) if _run_exists(project, pass_b_id) else None
    stored = project.connection.execute(
        "SELECT status,quality_json,created_at FROM book_maps WHERE map_run_id=?", (map_run_id,)
    ).fetchone()
    return {
        "map_run_id": map_run_id,
        "revision_uid": pass_a["revision_uid"],
        "pass_a": pass_a,
        "pass_b": pass_b,
        "ready_for_pass_b": pass_a["coverage_complete"],
        "ready_to_finalize": bool(pass_b and pass_b["coverage_complete"]),
        "finalized": stored is not None,
        "canonical_status": stored["status"] if stored else None,
        "editorial_quality": json.loads(stored["quality_json"]) if stored else {"status": "not_assessed"},
        "finalized_at": stored["created_at"] if stored else None,
    }


def _validate_pass_result(
    project: ManuscriptProject,
    *,
    map_run_id: str,
    pass_name: str,
    revision_uid: str,
    unit_id: str,
    payload: dict,
) -> dict:
    errors: list[str] = []
    if payload.get("schema_version") != MAP_SCHEMA_VERSION:
        errors.append("schema_version: unsupported pass-result schema")
    expected_values = {
        "map_run_id": map_run_id,
        "pass": pass_name,
        "revision_uid": revision_uid,
        "chapter_stable_id": unit_id,
    }
    for name, expected in expected_values.items():
        if payload.get(name) != expected:
            errors.append(f"{name}: expected {expected!r}")
    if not isinstance(payload.get("chapter_summary"), str) or not payload["chapter_summary"].strip():
        errors.append("chapter_summary: a non-empty summary is required, including for zero-finding chapters")
    record_errors, evidence_count, record_ids = _validate_records(
        project,
        revision_uid,
        payload.get("records"),
        strict=True,
        expected_chapter_id=unit_id,
        expected_pass=pass_name,
    )
    errors.extend(record_errors)
    reviewed = payload.get("reviewed_pass_a_record_ids", [])
    if pass_name == "B":
        if (
            not isinstance(reviewed, list)
            or not all(isinstance(item, str) and item for item in reviewed)
            or len(set(reviewed)) != len(reviewed)
        ):
            errors.append("reviewed_pass_a_record_ids: expected a unique string list")
    elif "reviewed_pass_a_record_ids" in payload:
        errors.append("reviewed_pass_a_record_ids is only valid in Pass B")
    return {
        "ok": not errors,
        "errors": errors,
        "record_ids": sorted(record_ids),
        "records_checked": len(record_ids),
        "evidence_checked": evidence_count,
    }


def advance_book_map(
    project: ManuscriptProject,
    map_run_id: str,
    *,
    actor: str = "agent",
) -> dict:
    """Validate completed Pass A results, then create Pass B in ER-014."""
    status = book_map_status(project, map_run_id)
    if not status["pass_a"]["coverage_complete"]:
        raise ValueError("Pass A coverage must be complete and intact before Pass B")
    for unit, payload in _load_run_results(project, status["pass_a"]["run_id"]):
        check = _validate_pass_result(
            project,
            map_run_id=map_run_id,
            pass_name="A",
            revision_uid=status["revision_uid"],
            unit_id=unit["unit_id"],
            payload=payload,
        )
        if not check["ok"]:
            raise ValueError("Invalid Pass A result: " + "; ".join(check["errors"]))
    project.create_run(
        _pass_run_id(map_run_id, "B"),
        status["revision_uid"],
        run_type="book-map-pass-b",
        scope="chapter",
        actor=actor,
        provenance={
            "book_map_run_id": map_run_id,
            "book_map_pass": "B",
            "pass_a_plan_hash": status["pass_a"]["plan"]["plan_hash"],
        },
    )
    return book_map_status(project, map_run_id)


def _compact_context(results: list[tuple[dict, dict]]) -> dict:
    return {
        "chapter_summaries": [
            {
                "chapter_stable_id": payload["chapter_stable_id"],
                "summary": payload["chapter_summary"],
            }
            for _, payload in results
        ],
        "records": [
            {
                key: record[key]
                for key in (
                    "id", "kind", "record_class", "epistemic_status", "scope",
                    "narrative_frame", "statement", "review_status", "links",
                    "sequence", "pov_type",
                )
                if key in record
            }
            for _, payload in results
            for record in payload.get("records", [])
        ],
    }


def claim_book_map_unit(
    project: ManuscriptProject,
    map_run_id: str,
    pass_name: str,
    *,
    worker_id: str,
    actor: str = "agent",
    include_text: bool = False,
) -> dict:
    """Claim one ER-014 unit and return bounded whole-book context for it."""
    pass_name = pass_name.upper()
    if pass_name not in {"A", "B"}:
        raise ValueError("Book-map pass must be A or B")
    status = book_map_status(project, map_run_id)
    if pass_name == "B" and not status["ready_for_pass_b"]:
        raise ValueError("Pass A must be complete before Pass B work can be claimed")
    run_id = _pass_run_id(map_run_id, pass_name)
    if not _run_exists(project, run_id):
        raise ValueError(f"Pass {pass_name} has not been created")
    claim = project.claim_run_unit(run_id, worker_id=worker_id, actor=actor)
    unit = claim["next_unit"]
    if unit is None:
        return {**claim, "map_status": book_map_status(project, map_run_id)}
    inventory = inventory_book(project, status["revision_uid"], include_text=False)
    chapter = next(item for item in inventory["chapters"] if item["stable_id"] == unit["unit_id"])
    if include_text:
        text_rows = project.connection.execute(
            "SELECT stable_id,source_order,chapter_order,text FROM paragraphs "
            "WHERE revision_uid=? AND chapter_stable_id=? ORDER BY source_order",
            (status["revision_uid"], unit["unit_id"]),
        )
        chapter = {**chapter, "paragraphs": [dict(row) for row in text_rows]}
    prior: list[tuple[dict, dict]] = []
    if pass_name == "A":
        prior = _load_run_results(project, run_id)
    else:
        prior = _load_run_results(project, status["pass_a"]["run_id"])
        prior += _load_run_results(project, run_id)
    instructions = (
        "Pass A: inventory structure, actors, aliases, relationships and global context. "
        "Keep all conclusions provisional and do not classify apparent errors before later chapters."
        if pass_name == "A"
        else
        "Pass B: inspect this chapter in the full Pass-A context. Check later payoffs, dreams, "
        "flashbacks, unreliable narration, cameos, nonlinear time, motifs and deliberate devices "
        "before confirming, disputing or leaving a question open."
    )
    return {
        **claim,
        "task": {
            "schema_version": MAP_SCHEMA_VERSION,
            "map_run_id": map_run_id,
            "pass": pass_name,
            "revision_uid": status["revision_uid"],
            "chapter": chapter,
            "whole_book_inventory": [
                {
                    "stable_id": item["stable_id"],
                    "source_order": item["source_order"],
                    "title": item["title"],
                    "paragraph_count": len(item["paragraphs"]),
                }
                for item in inventory["chapters"]
            ],
            "global_context": _compact_context(prior),
            "instructions": instructions,
            "manuscript_text_included": include_text,
        },
    }


def complete_book_map_unit(
    project: ManuscriptProject,
    map_run_id: str,
    pass_name: str,
    unit_id: str,
    *,
    attempt_uid: str,
    result: str | Path,
    actor: str = "agent",
) -> dict:
    """Validate a pass result before committing its ER-014 checkpoint."""
    pass_name = pass_name.upper()
    if pass_name not in {"A", "B"}:
        raise ValueError("Book-map pass must be A or B")
    status = book_map_status(project, map_run_id)
    run_id = _pass_run_id(map_run_id, pass_name)
    if not _run_exists(project, run_id):
        raise ValueError(f"Pass {pass_name} has not been created")
    payload = read_book_map(result)
    check = _validate_pass_result(
        project,
        map_run_id=map_run_id,
        pass_name=pass_name,
        revision_uid=status["revision_uid"],
        unit_id=unit_id,
        payload=payload,
    )
    if not check["ok"]:
        raise ValueError("Invalid book-map unit result: " + "; ".join(check["errors"]))
    completed = project.complete_run_unit(
        run_id,
        unit_id,
        attempt_uid=attempt_uid,
        result=result,
        actor=actor,
    )
    return {"validation": check, "run": completed, "map_status": book_map_status(project, map_run_id)}


def _readable_summary(records: list[dict]) -> dict:
    groups = {
        "premise": {"premise"},
        "narrative_structure": {"narrative_structure", "plotline"},
        "character_arcs": {"character", "goal", "change", "relationship", "alias"},
        "chronology_and_causality": {"scene", "event", "timeline_anchor", "causal_link"},
        "pov_and_narrative_frames": {"narrator_pov"},
        "genre_promises_and_payoffs": {"genre_expectation", "promise", "payoff"},
        "motifs_symbols_and_devices": {"motif", "symbol", "authorial_device", "story_rule"},
        "open_questions": {"open_question", "disagreement"},
    }
    return {
        name: [record["statement"] for record in records if record["kind"] in kinds]
        for name, kinds in groups.items()
    }


def _categorized(records: list[dict]) -> dict:
    return {
        "entities": [record for record in records if record["kind"] in {"character", "alias"}],
        "scenes": [record for record in records if record["kind"] == "scene"],
        "claims": [
            record for record in records
            if record["kind"] not in {
                "character", "alias", "scene", "relationship", "causal_link",
                "open_question", "disagreement",
            }
        ],
        "relations": [
            record for record in records if record["kind"] in {"relationship", "causal_link"}
        ],
        "unresolved_questions": [
            record for record in records if record["kind"] in {"open_question", "disagreement"}
        ],
        "editorial_hypotheses": [
            record for record in records if record["record_class"] == "editorial_hypothesis"
        ],
        "author_decisions": [
            record for record in records if record["record_class"] == "author_decision"
        ],
    }


def finalize_book_map(
    project: ManuscriptProject,
    map_run_id: str,
) -> dict:
    """Consolidate two intact ER-014 passes into the canonical SQLite map."""
    status = book_map_status(project, map_run_id)
    if status["finalized"]:
        payload = load_book_map(project, map_run_id)
        payload["idempotent"] = True
        return payload
    if not status["ready_to_finalize"]:
        raise ValueError("Both Pass A and Pass B must have complete, intact coverage")
    pass_results: dict[str, list[tuple[dict, dict]]] = {
        "A": _load_run_results(project, status["pass_a"]["run_id"]),
        "B": _load_run_results(project, status["pass_b"]["run_id"]),
    }
    all_records: list[dict] = []
    source_meta: list[tuple[str, str, dict]] = []
    seen: set[str] = set()
    pass_a_ids: set[str] = set()
    reviewed_a_ids: set[str] = set()
    for pass_name in ("A", "B"):
        for unit, payload in pass_results[pass_name]:
            check = _validate_pass_result(
                project,
                map_run_id=map_run_id,
                pass_name=pass_name,
                revision_uid=status["revision_uid"],
                unit_id=unit["unit_id"],
                payload=payload,
            )
            if not check["ok"]:
                raise ValueError(
                    f"Invalid Pass {pass_name} result for {unit['unit_id']}: "
                    + "; ".join(check["errors"])
                )
            if pass_name == "B":
                reviewed_a_ids.update(payload.get("reviewed_pass_a_record_ids", []))
            for record in payload["records"]:
                if record["id"] in seen:
                    raise ValueError(f"Duplicate record ID across pass results: {record['id']}")
                seen.add(record["id"])
                if pass_name == "A":
                    pass_a_ids.add(record["id"])
                normalized = {**record, "record_class": _record_class(record)}
                all_records.append(normalized)
                source_meta.append((pass_name, unit["unit_id"], normalized))
    unknown_reviewed = reviewed_a_ids - pass_a_ids
    missing_review = pass_a_ids - reviewed_a_ids
    if unknown_reviewed:
        raise ValueError("Pass B reviewed unknown Pass A record IDs: " + ", ".join(sorted(unknown_reviewed)))
    if missing_review:
        raise ValueError("Pass B did not review all Pass A records: " + ", ".join(sorted(missing_review)))
    for record in all_records:
        for link in record.get("links", []):
            if link["target_id"] not in seen:
                raise ValueError(
                    f"Record {record['id']} links to unknown record {link['target_id']}"
                )
    for record in all_records:
        if record["id"] in pass_a_ids:
            record["validated_in_pass_b"] = True
    revision = _source(project, status["revision_uid"])
    evidence_count = sum(len(record["evidence"]) for record in all_records)
    coverage = {
        "inventory_complete": True,
        "pass_a_complete": True,
        "pass_b_complete": True,
        "pass_a_units_verified": status["pass_a"]["verified_completed"],
        "pass_b_units_verified": status["pass_b"]["verified_completed"],
        "chapter_count": status["pass_a"]["unit_count"],
        "pass_a_records_reviewed": len(reviewed_a_ids),
        "record_count": len(all_records),
        "evidence_count": evidence_count,
        "validation_status": "validated",
    }
    quality = {
        "status": "not_assessed",
        "message": "Data integrity passed; editorial quality requires a separate measured assessment.",
    }
    created_at = utc_now()
    with project.connection:
        project.connection.execute(
            """INSERT INTO book_maps(
                map_run_id,revision_uid,schema_version,source_sha256,pass_a_run_id,
                pass_b_run_id,status,coverage_json,quality_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                map_run_id, status["revision_uid"], MAP_SCHEMA_VERSION,
                revision["source_sha256"], status["pass_a"]["run_id"],
                status["pass_b"]["run_id"], "validated",
                json.dumps(coverage, ensure_ascii=False, sort_keys=True),
                json.dumps(quality, ensure_ascii=False, sort_keys=True), created_at,
            ),
        )
        for pass_name, unit_id, record in source_meta:
            project.connection.execute(
                """INSERT INTO book_map_records(
                    map_run_id,record_id,source_pass,source_unit_id,kind,record_class,
                    epistemic_status,scope,narrative_frame,statement,record_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    map_run_id, record["id"], pass_name, unit_id, record["kind"],
                    record["record_class"], record["epistemic_status"], record["scope"],
                    record["narrative_frame"], record["statement"],
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                ),
            )
            for order, anchor in enumerate(record["evidence"]):
                project.connection.execute(
                    """INSERT INTO book_map_evidence(
                        map_run_id,record_id,evidence_order,revision_uid,
                        paragraph_stable_id,exact_quote,start_offset,end_offset
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        map_run_id, record["id"], order, anchor["revision_uid"],
                        anchor["paragraph_stable_id"], anchor["exact_quote"],
                        anchor.get("start_offset"), anchor.get("end_offset"),
                    ),
                )
        project.connection.execute(
            "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
            (
                "book_map.finalized", status["revision_uid"],
                json.dumps({"map_run_id": map_run_id, "coverage": coverage}, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )
    return load_book_map(project, map_run_id)


def load_book_map(project: ManuscriptProject, map_run_id: str) -> dict:
    map_run_id = _map_run_id(map_run_id)
    row = project.connection.execute(
        "SELECT * FROM book_maps WHERE map_run_id=?", (map_run_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Book map is not finalized: {map_run_id}")
    revision = _source(project, row["revision_uid"])
    if revision["source_sha256"] != row["source_sha256"]:
        raise ValueError("Canonical book map source SHA-256 mismatch")
    records = []
    for record_row in project.connection.execute(
        "SELECT source_pass,source_unit_id,record_json FROM book_map_records "
        "WHERE map_run_id=? ORDER BY rowid", (map_run_id,)
    ):
        record = json.loads(record_row["record_json"])
        record["source_pass"] = record_row["source_pass"]
        record["source_unit_id"] = record_row["source_unit_id"]
        records.append(record)
    inventory = inventory_book(project, row["revision_uid"], include_text=False)
    categorized = _categorized(records)
    payload = {
        **inventory,
        "schema_version": row["schema_version"],
        "map_run_id": map_run_id,
        "created_at": row["created_at"],
        "status": row["status"],
        "coverage": json.loads(row["coverage_json"]),
        "records": records,
        **categorized,
        "readable_summary": _readable_summary(records),
        "analysis_state": "complete",
        "source_inventory_complete": True,
        "editorial_analysis_complete": True,
        "integrity_validation": {
            "status": "validated",
            "source_sha256_verified": True,
            "evidence_verified": True,
        },
        "editorial_quality": json.loads(row["quality_json"]),
    }
    validation = validate_book_map(project, row["revision_uid"], payload)
    if not validation["ok"]:
        raise ValueError("Canonical book map integrity check failed: " + "; ".join(validation["errors"]))
    return payload


def record_quality_assessment(
    project: ManuscriptProject,
    map_run_id: str,
    assessment: dict,
    *,
    actor: str = "reviewer",
) -> dict:
    """Store a measured quality assessment separately from integrity validation."""
    load_book_map(project, map_run_id)
    errors: list[str] = []
    if assessment.get("assessment_type") not in {
        "synthetic_regression", "expert_review", "author_review"
    }:
        errors.append("assessment_type must be synthetic_regression, expert_review or author_review")
    if not isinstance(assessment.get("sample_size"), int) or assessment["sample_size"] < 1:
        errors.append("sample_size must be a positive integer")
    locales = assessment.get("locales")
    if not isinstance(locales, list) or not locales or not all(
        isinstance(item, str) and item.strip() for item in locales
    ):
        errors.append("locales must be a non-empty string list")
    metrics = assessment.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        errors.append("metrics must be a non-empty object of measured values")
    else:
        for name, value in metrics.items():
            if not isinstance(name, str) or not name.strip():
                errors.append("metric names must be non-empty strings")
                continue
            if isinstance(value, bool):
                errors.append(f"metric {name} must be numeric or a passed/total count")
            elif isinstance(value, float):
                if not 0 <= value <= 1:
                    errors.append(f"fractional metric {name} must be in the range 0..1")
            elif isinstance(value, int):
                if value < 0:
                    errors.append(f"count metric {name} cannot be negative")
            elif isinstance(value, dict):
                passed, total = value.get("passed"), value.get("total")
                if (
                    not isinstance(passed, int) or isinstance(passed, bool)
                    or not isinstance(total, int) or isinstance(total, bool)
                    or total < 1 or passed < 0 or passed > total
                ):
                    errors.append(f"metric {name} has invalid passed/total counts")
            else:
                errors.append(f"metric {name} must be numeric or a passed/total count")
    if assessment.get("assessment_type") == "author_review" and assessment.get("consent_confirmed") is not True:
        errors.append("author_review requires consent_confirmed=true")
    if not isinstance(assessment.get("limitations"), list):
        errors.append("limitations must be a list")
    if errors:
        raise ValueError("Invalid editorial quality assessment: " + "; ".join(errors))
    stored = {**assessment, "status": "assessed", "recorded_at": utc_now(), "actor": actor}
    with project.connection:
        project.connection.execute(
            "UPDATE book_maps SET quality_json=? WHERE map_run_id=?",
            (json.dumps(stored, ensure_ascii=False, sort_keys=True), map_run_id),
        )
        revision_uid = project.connection.execute(
            "SELECT revision_uid FROM book_maps WHERE map_run_id=?", (map_run_id,)
        ).fetchone()[0]
        project.connection.execute(
            "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
            (
                "book_map.quality_assessed", revision_uid,
                json.dumps({"map_run_id": map_run_id, "assessment": stored}, ensure_ascii=False, sort_keys=True),
                stored["recorded_at"],
            ),
        )
    return load_book_map(project, map_run_id)
