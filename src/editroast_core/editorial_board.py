"""ER-021: validate three editorial lenses and prepare an evidence-led board preview.

No model inference, manuscript edits, automatic approval, or network access.
This first slice is intentionally stateless; ER-014 orchestration is a later step.
"""
from __future__ import annotations

from collections import defaultdict

from .book_map import load_book_map, _source_paragraphs
from .manuscript_state import ManuscriptProject

REVIEW_SCHEMA_VERSION = 1
PERSONAS = ("reader_market", "voice_emotion", "structure_logic")
PERSONA_NAMES = {
    "reader_market": "Leo Shelf",
    "voice_emotion": "Vera Sharp",
    "structure_logic": "Boris Marker",
}
KINDS = frozenset({"strength", "concern", "opportunity"})
SEVERITIES = {"none": 0, "minor": 1, "moderate": 2, "major": 3, "critical": 4}
MAX_FINDINGS_PER_LENS = 1000
MAX_EVIDENCE_PER_FINDING = 24


def _plain(value: object, label: str, errors: list[str], *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        errors.append(f"{label}: expected nonempty text")
        return ""
    return value


def _check_evidence(
    anchor: object, revision_uid: str, paragraphs: dict, label: str, errors: list[str],
) -> None:
    if not isinstance(anchor, dict):
        errors.append(f"{label}: expected a source anchor")
        return
    if anchor.get("revision_uid") != revision_uid:
        errors.append(f"{label}: revision mismatch")
    stable_id = anchor.get("paragraph_stable_id")
    paragraph = paragraphs.get(stable_id) if isinstance(stable_id, str) else None
    if paragraph is None:
        errors.append(f"{label}: source paragraph not found")
        return
    quote = anchor.get("exact_quote")
    if not isinstance(quote, str) or not quote.strip():
        errors.append(f"{label}: exact quotation is required")
        return
    content = paragraph["text"]
    start, end = anchor.get("start_offset"), anchor.get("end_offset")
    if (start is None) != (end is None):
        errors.append(f"{label}: source offsets must be paired")
    elif start is not None:
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end <= start or end > len(content)
            or content[start:end] != quote
        ):
            errors.append(f"{label}: quote offsets do not match the original")
    elif quote not in content:
        errors.append(f"{label}: exact quotation not found in the original")


def preview_editorial_board(
    project: ManuscriptProject, map_run_id: str, submission: dict,
) -> dict:
    """Fail closed on broken citations; group only explicit root-cause IDs.

    Duplicate root causes are *not* discovered semantically. Opposing reviews
    stay visible and unresolved until an editorial board explicitly evaluates them.
    """
    book_map = load_book_map(project, map_run_id)
    errors: list[str] = []
    if not isinstance(submission, dict):
        raise ValueError("Editorial submission must be a JSON object")
    revision_uid = book_map["revision_uid"]
    if type(submission.get("schema_version")) is not int or submission["schema_version"] != REVIEW_SCHEMA_VERSION:
        errors.append("schema_version: expected editorial schema v1")
    if submission.get("map_run_id") != map_run_id:
        errors.append("map_run_id: does not match the finalized whole-book map")
    if submission.get("revision_uid") != revision_uid:
        errors.append("revision_uid: does not match the map source revision")
    if submission.get("source_sha256") != book_map["source_sha256"]:
        errors.append("source_sha256: does not match the immutable source")
    reviews = submission.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != len(PERSONAS):
        raise ValueError("Exactly three lens reviews are required")
    paragraphs = _source_paragraphs(project, revision_uid)
    map_record_ids = {record["id"] for record in book_map["records"]}
    seen_roles: set[str] = set()
    seen_findings: set[str] = set()
    grouped: dict[str, list[dict]] = defaultdict(list)
    rendered_reviews: dict[str, dict] = {}
    for review_index, review in enumerate(reviews):
        label = f"reviews[{review_index}]"
        if not isinstance(review, dict):
            errors.append(f"{label}: expected a review object")
            continue
        role = review.get("persona_id")
        if not isinstance(role, str) or role not in PERSONAS:
            errors.append(f"{label}: unknown editor persona")
            continue
        if role in seen_roles:
            errors.append(f"{label}: duplicate editor persona")
        seen_roles.add(role)
        summary = _plain(review.get("summary"), f"{label}.summary", errors)
        findings = review.get("findings")
        if not isinstance(findings, list) or len(findings) > MAX_FINDINGS_PER_LENS:
            errors.append(f"{label}.findings: expected a list of at most {MAX_FINDINGS_PER_LENS}")
            continue
        safe_findings = []
        for find_index, item in enumerate(findings):
            prefix = f"{label}.findings[{find_index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix}: expected a finding object")
                continue
            fid = _plain(item.get("id"), f"{prefix}.id", errors)
            cause = _plain(item.get("root_cause_id"), f"{prefix}.root_cause_id", errors)
            if fid in seen_findings:
                errors.append(f"{prefix}: duplicate global finding ID")
            seen_findings.add(fid)
            kind = item.get("kind")
            if not isinstance(kind, str) or kind not in KINDS:
                errors.append(f"{prefix}: invalid finding kind")
            severity = item.get("severity")
            if not isinstance(severity, str) or severity not in SEVERITIES:
                errors.append(f"{prefix}: invalid severity")
            if kind == "strength" and severity != "none":
                errors.append(f"{prefix}: strength severity must be none")
            if kind != "strength" and severity == "none":
                errors.append(f"{prefix}: issue severity cannot be none")
            statement = _plain(item.get("statement"), f"{prefix}.statement", errors)
            rationale = _plain(item.get("rationale"), f"{prefix}.rationale", errors)
            recommendation = item.get("recommendation", "")
            if not isinstance(recommendation, str):
                errors.append(f"{prefix}.recommendation: expected text")
                recommendation = ""
            refs = item.get("map_record_ids", [])
            if not isinstance(refs, list) or len(refs) != len(set(x for x in refs if isinstance(x, str))) or any(
                not isinstance(x, str) or x not in map_record_ids for x in refs
            ):
                errors.append(f"{prefix}.map_record_ids: unknown or duplicated map record ID")
                refs = []
            anchors = item.get("evidence")
            if not isinstance(anchors, list) or not 1 <= len(anchors) <= MAX_EVIDENCE_PER_FINDING:
                errors.append(f"{prefix}.evidence: at least one and at most {MAX_EVIDENCE_PER_FINDING} source anchors required")
                anchors = []
            for anchor_idx, anchor in enumerate(anchors):
                _check_evidence(anchor, revision_uid, paragraphs, f"{prefix}.evidence[{anchor_idx}]", errors)
            if isinstance(kind, str) and kind in KINDS and isinstance(severity, str) and severity in SEVERITIES:
                safe = {
                    "id": fid,
                    "root_cause_id": cause,
                    "persona_id": role,
                    "kind": kind,
                    "severity": severity,
                    "statement": statement,
                    "rationale": rationale,
                    "recommendation": recommendation,
                    "evidence": anchors,
                    "map_record_ids": refs,
                    "author_decision_status": "pending",
                    "applied": False,
                }
                safe_findings.append(safe)
                grouped[cause].append(safe)
        rendered_reviews[role] = {"persona_id": role, "name": PERSONA_NAMES[role], "summary": summary, "findings": safe_findings}
    if seen_roles != set(PERSONAS):
        errors.append("reviews: all three independent editorial lenses must be present")
    if errors:
        return {"ok": False, "errors": errors, "map_run_id": map_run_id, "revision_uid": revision_uid}
    root_causes = []
    for cause, findings in grouped.items():
        kinds = {item["kind"] for item in findings}
        issue_items = [f for f in findings if f["kind"] != "strength"]
        severity = max((SEVERITIES[f["severity"]] for f in issue_items), default=0)
        name = next(name for name, score in SEVERITIES.items() if score == severity)
        root_causes.append({
            "id": cause,
            "severity": name,
            "priority": severity,
            "finding_ids": [f["id"] for f in findings],
            "supporting_personas": sorted({f["persona_id"] for f in findings}),
            "requires_board_resolution": "strength" in kinds and bool(issue_items),
            "dissent_preserved": "strength" in kinds and bool(issue_items),
            "status": "pending_board_review",
        })
    root_causes.sort(key=lambda r: (-r["priority"], r["id"]))
    correction_list = []
    for root in root_causes:
        for finding in grouped[root["id"]]:
            if finding["kind"] == "strength":
                continue
            correction_list.append({
                "finding_id": finding["id"],
                "root_cause_id": root["id"],
                "severity": finding["severity"],
                "recommendation": finding["recommendation"],
                "evidence": finding["evidence"],
                "author_decision_status": "pending",
                "applied": False,
            })
    return {
        "ok": True,
        "schema_version": REVIEW_SCHEMA_VERSION,
        "map_run_id": map_run_id,
        "revision_uid": revision_uid,
        "editorial_quality_assessed": False,
        "editors": [rendered_reviews[role] for role in PERSONAS],
        "editorial_board": {
            "persona_id": "editorial_board",
            "chair_name_en": "Chief Sterling",
            "independent_fourth_review": False,
            "verdict": "pending_board_synthesis",
            "root_causes": root_causes,
            "unresolved_disagreements": [r["id"] for r in root_causes if r["requires_board_resolution"]],
        },
        "correction_list": correction_list,
        "changes_applied": False,
        "author_approval_recorded": False,
    }
