"""ER-021 S3: explicit, evidence-preserving Editorial Board adjudication.

The board's synthesis is supplied by an authorized agent/reviewer. Python
validates coverage, deterministic grouping, dissent, source provenance and
dependency graphs; it does not decide artistic merit or apply manuscript edits.
"""
from __future__ import annotations

from .book_map import load_book_map
from .editorial_board import SEVERITIES, preview_completed_editorial_lenses
from .manuscript_state import ManuscriptProject

DECISION_SCHEMA_VERSION = 1
DISPOSITIONS = frozenset({
    "revise", "preserve_intent", "monitor", "needs_author_decision", "no_action",
})
CATEGORIES = frozenset({"objective", "subjective", "mixed", "not_applicable"})
VERDICTS = frozenset({
    "revision_recommended", "ready_for_author_review", "needs_author_decision",
    "no_changes_recommended",
})


def _required_text(value: object, field: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        errors.append(f"{field}: nonempty text (max 10000 characters) required")
        return ""
    return value.strip()


def _unique_strings(value: object, field: str, errors: list[str], *, nonempty: bool = True) -> list[str]:
    if (not isinstance(value, list)
            or (nonempty and not value)
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        errors.append(f"{field}: expected {'nonempty ' if nonempty else ''}list of nonempty strings")
        return []
    if len(set(value)) != len(value):
        errors.append(f"{field}: duplicate references")
    return value


def _validate_dag(groups: dict[str, dict], errors: list[str]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str) -> None:
        if node in visiting:
            errors.append(f"dependency cycle including {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for other in groups[node]["depends_on"]:
            if other in groups:
                walk(other)
        visiting.remove(node)
        visited.add(node)

    for node in groups:
        walk(node)


def adjudicate_editorial_board(
    project: ManuscriptProject, map_run_id: str, review_id: str,
    decision: dict,
) -> dict:
    """Validate complete, three-lens board evidence and an explicit chair decision.

    Every preview root cause must appear in exactly one adjudicated group.
    Apparent synonyms may be merged only by an explicit reviewer rationale;
    no automatic semantic deduplication or author consent is inferred.
    """
    preview = preview_completed_editorial_lenses(project, map_run_id, review_id)
    book = load_book_map(project, map_run_id)
    if not isinstance(decision, dict):
        raise ValueError("Board adjudication must be a JSON object")
    errors: list[str] = []
    expected = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "map_run_id": map_run_id,
        "review_id": review_id,
        "revision_uid": book["revision_uid"],
        "source_sha256": book["source_sha256"],
        "chair_persona_id": "editorial_board",
    }
    for name, value in expected.items():
        if decision.get(name) != value or (name == "schema_version" and type(decision.get(name)) is not int):
            errors.append(f"{name}: mismatches completed editorial review")
    verdict = decision.get("verdict")
    if not isinstance(verdict, dict):
        errors.append("verdict: an explicit chair verdict object is required")
        verdict = {}
    classification = verdict.get("classification")
    if not isinstance(classification, str) or classification not in VERDICTS:
        errors.append("verdict.classification: unsupported overall verdict")
    summary = _required_text(verdict.get("summary"), "verdict.summary", errors)
    rationale = _required_text(verdict.get("rationale"), "verdict.rationale", errors)
    source_roots = {
        root["id"]: root for root in preview["editorial_board"]["root_causes"]
    }
    decisions = decision.get("groups")
    if not isinstance(decisions, list) or len(decisions) > len(source_roots):
        errors.append("groups: expected at most one decision group per original root cause")
        decisions = []
    covered: set[str] = set()
    accepted: dict[str, dict] = {}
    source_to_canonical: dict[str, str] = {}
    for index, item in enumerate(decisions):
        label = f"groups[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected a group object")
            continue
        cid = _required_text(item.get("canonical_id"), f"{label}.canonical_id", errors)
        if cid in accepted:
            errors.append(f"{label}: duplicate canonical ID")
        roots = _unique_strings(item.get("source_root_cause_ids"), f"{label}.source_root_cause_ids", errors)
        for rid in roots:
            if rid not in source_roots:
                errors.append(f"{label}: unknown source root cause {rid}")
            if rid in covered:
                errors.append(f"{label}: root cause {rid} assigned more than once")
            covered.add(rid)
            source_to_canonical[rid] = cid
        merge_rationale = item.get("merge_rationale", "")
        if len(roots) > 1:
            merge_rationale = _required_text(merge_rationale, f"{label}.merge_rationale", errors)
        elif not isinstance(merge_rationale, str):
            errors.append(f"{label}.merge_rationale: expected text")
            merge_rationale = ""
        disposition = item.get("disposition")
        if not isinstance(disposition, str) or disposition not in DISPOSITIONS:
            errors.append(f"{label}.disposition: unsupported disposition")
        category = item.get("category")
        if not isinstance(category, str) or category not in CATEGORIES:
            errors.append(f"{label}.category: unsupported classification")
        assessment = _required_text(item.get("assessment"), f"{label}.assessment", errors)
        reason = _required_text(item.get("rationale"), f"{label}.rationale", errors)
        recommendation = item.get("recommendation", "")
        if not isinstance(recommendation, str):
            errors.append(f"{label}.recommendation: expected text")
            recommendation = ""
        if disposition in {"revise", "needs_author_decision"}:
            _required_text(recommendation, f"{label}.recommendation", errors)
        dissent = item.get("dissent_resolution", "")
        if not isinstance(dissent, str):
            errors.append(f"{label}.dissent_resolution: expected text")
            dissent = ""
        if any(source_roots.get(rid, {}).get("requires_board_resolution") for rid in roots):
            _required_text(dissent, f"{label}.dissent_resolution", errors)
        depends = _unique_strings(item.get("depends_on", []), f"{label}.depends_on", errors, nonempty=False)
        conflicts = _unique_strings(item.get("conflicts_with", []), f"{label}.conflicts_with", errors, nonempty=False)
        if cid in depends or cid in conflicts:
            errors.append(f"{label}: a group cannot depend on or conflict with itself")
        if set(depends) & set(conflicts):
            errors.append(f"{label}: one group cannot both depend on and conflict with another")
        findings = [
            finding for editor in preview["editors"]
            for finding in editor["findings"] if finding["root_cause_id"] in roots
        ]
        severity_level = max(
            (SEVERITIES[finding["severity"]] for finding in findings if finding["kind"] != "strength"),
            default=0,
        )
        severity = next(name for name, number in SEVERITIES.items() if number == severity_level)
        evidence = []
        observed_anchors: set[tuple] = set()
        for finding in findings:
            for anchor in finding["evidence"]:
                fingerprint = (
                    anchor.get("revision_uid"), anchor.get("paragraph_stable_id"),
                    anchor.get("exact_quote"), anchor.get("start_offset"), anchor.get("end_offset"),
                )
                if fingerprint not in observed_anchors:
                    observed_anchors.add(fingerprint)
                    evidence.append(anchor)
        accepted[cid] = {
            "canonical_id": cid, "source_root_cause_ids": roots,
            "finding_ids": [finding["id"] for finding in findings],
            "supporting_personas": sorted({finding["persona_id"] for finding in findings}),
            "severity": severity, "priority": severity_level,
            "disposition": disposition, "category": category,
            "assessment": assessment, "rationale": reason,
            "merge_rationale": merge_rationale,
            "dissent_resolution": dissent,
            "recommendation": recommendation,
            "depends_on": depends, "conflicts_with": conflicts,
            "evidence": evidence,
            "author_decision_status": "pending",
            "applied": False,
        }
    missing = set(source_roots) - covered
    if missing:
        errors.append("groups: root causes not adjudicated: " + ", ".join(sorted(missing)))
    for cid, item in accepted.items():
        for field in ("depends_on", "conflicts_with"):
            for link in item[field]:
                if link not in accepted:
                    errors.append(f"{cid}.{field}: unknown canonical group {link}")
    if not errors:
        _validate_dag(accepted, errors)
    if errors:
        return {
            "ok": False, "errors": errors, "map_run_id": map_run_id,
            "review_id": review_id, "revision_uid": book["revision_uid"],
            "board_adjudication_complete": False,
        }
    # Dependencies precede dependents; source severity orders unrelated groups.
    ordered: list[dict] = []
    done: set[str] = set()

    def place(cid: str) -> None:
        if cid in done:
            return
        for dependency in sorted(accepted[cid]["depends_on"]):
            place(dependency)
        ordered.append(accepted[cid])
        done.add(cid)

    for cid in sorted(accepted, key=lambda key: (-accepted[key]["priority"], key)):
        place(cid)
    correction_list = [
        {
            "correction_id": item["canonical_id"],
            "root_cause_ids": item["source_root_cause_ids"],
            "finding_ids": item["finding_ids"],
            "severity": item["severity"],
            "category": item["category"],
            "proposed_change": item["recommendation"],
            "rationale": item["rationale"],
            "evidence": item["evidence"],
            "depends_on": item["depends_on"],
            "conflicts_with": item["conflicts_with"],
            "author_decision_status": "pending",
            "applied": False,
            "er013_plan_created": False,
        }
        for item in ordered if item["disposition"] in {"revise", "needs_author_decision"}
    ]
    return {
        "ok": True, "schema_version": DECISION_SCHEMA_VERSION,
        "map_run_id": map_run_id, "review_id": review_id,
        "revision_uid": book["revision_uid"],
        "source_sha256": book["source_sha256"],
        "editors": preview["editors"],
        "editorial_board": {
            "persona_id": "editorial_board", "chair_name_en": "Chief Sterling",
            "independent_fourth_review": False,
            "verdict": {
                "classification": classification, "summary": summary,
                "rationale": rationale, "provided_by": "authorized_editorial_agent",
                "human_quality_verified": False,
            },
            "groups": ordered,
            "unresolved_author_decisions": [
                item["canonical_id"] for item in ordered
                if item["disposition"] == "needs_author_decision"
            ],
            "dissent_preserved": any(item["dissent_resolution"] for item in ordered),
        },
        "correction_list": correction_list,
        "board_adjudication_complete": True,
        "editorial_quality_assessed": False,
        "author_approval_recorded": False,
        "changes_applied": False,
        "er013_safe_apply_invoked": False,
    }
