"""Source-bound whole-book inventory and editorial map validation (ER-020).

This module does not produce literary judgments or call a model. Manuscript
content and submitted records are untrusted data, never instructions.
"""
from __future__ import annotations

import json
from pathlib import Path

from .manuscript_state import ManuscriptProject, _safe_project_relative, sha256_file

MAP_SCHEMA_VERSION = 1
MAX_MAP_BYTES = 8 * 1024 * 1024
RECORD_KINDS = frozenset({
    "character", "alias", "relationship", "scene", "narrator_pov", "goal",
    "change", "event", "timeline_anchor", "causal_link", "promise", "payoff",
    "motif", "symbol", "story_rule", "genre_expectation",
})
EPISTEMIC_LABELS = frozenset({"observed", "inferred", "uncertain", "author_confirmed"})
NARRATIVE_FRAMES = frozenset({
    "literal", "dream", "imagined", "recollection", "unreliable_narration",
    "ambiguous",
})
SCOPES = frozenset({"chapter", "scene", "cross_book"})


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


def inventory_book(project: ManuscriptProject, revision_uid: str, *, include_text: bool = False) -> dict:
    """Deterministic inventory; text is opt-in to avoid accidental disclosure."""
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
            item = {"stable_id": paragraph["stable_id"], "source_order": paragraph["source_order"]}
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


def read_book_map(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_MAP_BYTES:
        raise ValueError("Book map file is missing or exceeds the 8 MiB limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid book map JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Book map must be a JSON object")
    return value


def validate_book_map(project: ManuscriptProject, revision_uid: str, payload: dict) -> dict:
    """Verify provenance and evidence; do NOT certify editorial quality."""
    expected = inventory_book(project, revision_uid, include_text=False)
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise ValueError("Book map must be a JSON object")
    for name in ("schema_version", "project_id", "revision_uid", "source_sha256"):
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
        refs = chapter.get("paragraphs")
        if not isinstance(refs, list):
            errors.append(f"chapters: missing paragraph inventory for {cid}")
            continue
        got = [item.get("stable_id") if isinstance(item, dict) else None for item in refs]
        want = [item["stable_id"] for item in original["paragraphs"]]
        if got != want:
            errors.append(f"chapters: paragraph coverage/order mismatch for {cid}")
    if seen_chapters != set(expected_chapters):
        errors.append("chapters: missing or additional chapter IDs")
    rows = project.connection.execute(
        "SELECT stable_id,text FROM paragraphs WHERE revision_uid=?", (revision_uid,)
    )
    paragraph_text = {row["stable_id"]: row["text"] for row in rows}
    records = payload.get("records")
    if not isinstance(records, list):
        errors.append("records: expected a list")
        records = []
    seen_records: set[str] = set()
    evidence_count = 0
    for i, record in enumerate(records):
        prefix = f"records[{i}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        rid = record.get("id")
        if not isinstance(rid, str) or not rid.strip() or rid in seen_records:
            errors.append(f"{prefix}: missing or duplicate record ID")
        else:
            seen_records.add(rid)
        if not isinstance(record.get("kind"), str) or record["kind"] not in RECORD_KINDS:
            errors.append(f"{prefix}: unsupported record kind")
        if not isinstance(record.get("epistemic_status"), str) or record["epistemic_status"] not in EPISTEMIC_LABELS:
            errors.append(f"{prefix}: unsupported epistemic status")
        if not isinstance(record.get("scope"), str) or record["scope"] not in SCOPES:
            errors.append(f"{prefix}: unsupported scope")
        if not isinstance(record.get("narrative_frame"), str) or record["narrative_frame"] not in NARRATIVE_FRAMES:
            errors.append(f"{prefix}: unsupported narrative frame")
        if not isinstance(record.get("statement"), str) or not record["statement"].strip():
            errors.append(f"{prefix}: missing statement")
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}: at least one evidence anchor is required")
            continue
        for j, anchor in enumerate(evidence):
            ap = f"{prefix}.evidence[{j}]"
            if not isinstance(anchor, dict):
                errors.append(f"{ap}: malformed evidence")
                continue
            if anchor.get("revision_uid") != revision_uid:
                errors.append(f"{ap}: revision mismatch")
            paragraph_id = anchor.get("paragraph_stable_id")
            para = paragraph_text.get(paragraph_id) if isinstance(paragraph_id, str) else None
            quote = anchor.get("exact_quote")
            if para is None:
                errors.append(f"{ap}: unknown paragraph stable ID")
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{ap}: empty quotation")
            elif para is not None and quote not in para:
                errors.append(f"{ap}: quoted text does not match source")
            else:
                if para is not None:
                    evidence_count += 1
    return {
        "ok": not errors,
        "errors": errors,
        "revision_uid": revision_uid,
        "source_inventory_complete": not any(e.startswith("chapters:") for e in errors),
        "source_evidence_verified": not errors,
        "editorial_analysis_complete": False,
        "editorial_quality_assessed": False,
        "records_checked": len(records),
        "evidence_checked": evidence_count,
    }
