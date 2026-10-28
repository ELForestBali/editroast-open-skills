"""Conservative structural revision diff planning for ER-012.

The scores in this module measure evidence for structural correspondence. They
are not claims about authorial intent or semantic equivalence.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import re
import uuid
from dataclasses import dataclass


WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?%?")
NEGATIONS = {
    "not", "no", "never", "none", "neither", "nor", "without", "nobody", "nothing",
    "не", "нет", "ни", "никогда", "без", "никто", "ничто",
    "non", "ne", "pas", "jamais", "sans",
    "kein", "keine", "nicht", "nie", "ohne",
    "no", "nunca", "jamás", "sin",
}


@dataclass
class RevisionDiffPlan:
    paragraph_records: list[dict]
    compatibility_changes: list[dict]
    groups: list[dict]
    candidates: list[dict]


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_RE.finditer(text)]


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(tokens(left))
    right_tokens = set(tokens(right))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def text_similarity(left: str, right: str) -> float:
    left_normal = normalize(left)
    right_normal = normalize(right)
    character = difflib.SequenceMatcher(None, left_normal, right_normal, autojunk=False).ratio()
    return round(character * 0.68 + _token_similarity(left, right) * 0.32, 6)


def _capitalized_terms(text: str) -> set[str]:
    result: set[str] = set()
    for match in WORD_RE.finditer(text):
        value = match.group(0)
        if value[:1].isupper():
            result.add(value.casefold())
    return result


def semantic_signals(before: list[str], after: list[str]) -> dict:
    """Return lexical risk signals, never a semantic-intent verdict."""
    before_text = "\n".join(before)
    after_text = "\n".join(after)
    before_numbers = NUMBER_RE.findall(before_text)
    after_numbers = NUMBER_RE.findall(after_text)
    before_words = tokens(before_text)
    after_words = tokens(after_text)
    before_negations = [word for word in before_words if word in NEGATIONS]
    after_negations = [word for word in after_words if word in NEGATIONS]
    before_caps = sorted(_capitalized_terms(before_text))
    after_caps = sorted(_capitalized_terms(after_text))
    stripped_before = " ".join(WORD_RE.findall(before_text.casefold()))
    stripped_after = " ".join(WORD_RE.findall(after_text.casefold()))
    return {
        "text_changed": normalize(before_text) != normalize(after_text),
        "punctuation_or_spacing_only": (
            normalize(before_text) != normalize(after_text)
            and stripped_before == stripped_after
        ),
        "numbers_changed": before_numbers != after_numbers,
        "numbers_before": before_numbers,
        "numbers_after": after_numbers,
        "negation_tokens_changed": before_negations != after_negations,
        "negations_before": before_negations,
        "negations_after": after_negations,
        "capitalized_terms_changed": before_caps != after_caps,
        "capitalized_terms_before": before_caps,
        "capitalized_terms_after": after_caps,
        "semantic_interpretation": "not_evaluated",
    }


def confidence_label(value: float) -> str:
    if value >= 0.9:
        return "high"
    if value >= 0.7:
        return "medium"
    return "low"


def _occurrences(values: list[str]) -> list[int]:
    seen: dict[str, int] = {}
    result: list[int] = []
    for value in values:
        seen[value] = seen.get(value, 0) + 1
        result.append(seen[value])
    return result


def _chapter_for_order(chapters: list, order: int) -> int:
    for index, chapter in enumerate(chapters):
        if chapter.start_paragraph <= order <= chapter.end_paragraph:
            return index
    return max(0, len(chapters) - 1)


def _lis_member_positions(pairs: list[tuple[int, int]]) -> set[int]:
    """Return pair-list positions belonging to one longest increasing subsequence."""
    tails: list[int] = []
    tails_positions: list[int] = []
    previous = [-1] * len(pairs)
    for position, (_, old_index) in enumerate(pairs):
        slot = bisect.bisect_left(tails, old_index)
        if slot == len(tails):
            tails.append(old_index)
            tails_positions.append(position)
        else:
            tails[slot] = old_index
            tails_positions[slot] = position
        if slot:
            previous[position] = tails_positions[slot - 1]
    if not tails_positions:
        return set()
    member_positions: set[int] = set()
    cursor = tails_positions[-1]
    while cursor >= 0:
        member_positions.add(cursor)
        cursor = previous[cursor]
    return member_positions


def _pair_score(
    old: dict,
    new_text: str,
    new_index: int,
    new_fps: list[str],
    new_chapter_id: str,
    total_new: int,
    total_old: int,
) -> tuple[float, dict]:
    similarity = text_similarity(old["text"], new_text)
    before = new_fps[new_index - 1] if new_index else None
    after = new_fps[new_index + 1] if new_index + 1 < total_new else None
    context_matches = int(bool(before and old.get("context_before") == before)) + int(
        bool(after and old.get("context_after") == after)
    )
    same_chapter = old.get("chapter_stable_id") == new_chapter_id
    old_relative = old["source_order"] / max(1, total_old - 1)
    new_relative = new_index / max(1, total_new - 1)
    position_score = max(0.0, 1.0 - abs(old_relative - new_relative))
    score = min(
        1.0,
        similarity * 0.74
        + context_matches * 0.06
        + (0.07 if same_chapter else 0.0)
        + position_score * 0.07,
    )
    return round(score, 6), {
        "text_similarity": similarity,
        "context_matches": context_matches,
        "same_chapter": same_chapter,
        "position_score": round(position_score, 6),
    }


def _candidate_old_indexes(
    old: list[dict],
    old_token_index: dict[str, set[int]],
    new_text: str,
    new_index: int,
    new_chapter_id: str,
    total_new: int,
) -> set[int]:
    candidates: set[int] = set()
    expected = round(new_index * max(0, len(old) - 1) / max(1, total_new - 1))
    candidates.update(range(max(0, expected - 24), min(len(old), expected + 25)))
    candidates.update(
        index for index, row in enumerate(old) if row.get("chapter_stable_id") == new_chapter_id
    )
    for token in {token for token in tokens(new_text) if len(token) > 3}:
        candidates.update(old_token_index.get(token, set()))
    return candidates


def plan_paragraph_diff(
    paragraphs: list,
    chapter_ids: list[str],
    chapters: list,
    old_rows: list,
) -> RevisionDiffPlan:
    old = [dict(row) for row in old_rows]
    new_fps = [text_fingerprint(paragraph.text) for paragraph in paragraphs]
    occurrences = _occurrences(new_fps)
    old_by_fp: dict[str, list[int]] = {}
    new_by_fp: dict[str, list[int]] = {}
    for index, row in enumerate(old):
        old_by_fp.setdefault(row["text_fingerprint"], []).append(index)
    for index, value in enumerate(new_fps):
        new_by_fp.setdefault(value, []).append(index)

    assignments: dict[int, dict] = {}
    used_old: set[int] = set()
    used_new: set[int] = set()
    raw_groups: list[dict] = []
    raw_candidates: list[dict] = []

    def add_group(
        relation_type: str,
        before_indexes: list[int],
        after_indexes: list[int],
        confidence: float,
        *,
        status: str = "auto",
        details: dict | None = None,
    ) -> dict:
        group = {
            "group_uid": _uid("dg"),
            "entity_type": "paragraph",
            "relation_type": relation_type,
            "confidence": round(confidence, 6),
            "status": status,
            "before_indexes": before_indexes,
            "after_indexes": after_indexes,
            "semantic_signals": semantic_signals(
                [old[index]["text"] for index in before_indexes],
                [paragraphs[index].text for index in after_indexes],
            ),
            "details": details or {},
        }
        raw_groups.append(group)
        return group

    # Exact unique and stable-occurrence matches, including long-distance moves.
    for value, new_indexes in new_by_fp.items():
        old_indexes = old_by_fp.get(value, [])
        if len(new_indexes) == len(old_indexes) == 1:
            new_index, old_index = new_indexes[0], old_indexes[0]
            assignments[new_index] = {"old_indexes": [old_index], "stable_id": old[old_index]["stable_id"], "kind": "exact"}
            used_new.add(new_index)
            used_old.add(old_index)
            add_group("unchanged", [old_index], [new_index], 1.0, details={"exact_text": True})
        elif old_indexes and len(new_indexes) == len(old_indexes):
            for new_index, old_index in zip(new_indexes, old_indexes):
                assignments[new_index] = {"old_indexes": [old_index], "stable_id": old[old_index]["stable_id"], "kind": "exact_repeat"}
                used_new.add(new_index)
                used_old.add(old_index)
                add_group(
                    "unchanged", [old_index], [new_index], 0.96,
                    details={"exact_text": True, "repeat_occurrence": occurrences[new_index]},
                )

    # Candidate narrowing index for modified one-to-one matches.
    old_token_index: dict[str, set[int]] = {}
    for old_index, row in enumerate(old):
        for token in {token for token in tokens(row["text"]) if len(token) > 3}:
            old_token_index.setdefault(token, set()).add(old_index)
    pair_proposals: dict[int, list[tuple[float, int, dict]]] = {}
    for new_index, paragraph in enumerate(paragraphs):
        if new_index in used_new or not normalize(paragraph.text):
            continue
        chapter_id = chapter_ids[_chapter_for_order(chapters, new_index)]
        for old_index in _candidate_old_indexes(
            old, old_token_index, paragraph.text, new_index, chapter_id, len(paragraphs)
        ):
            if old_index in used_old or old[old_index]["text_fingerprint"] == new_fps[new_index]:
                continue
            score, signals = _pair_score(
                old[old_index], paragraph.text, new_index, new_fps, chapter_id,
                len(paragraphs), len(old),
            )
            if score >= 0.44:
                pair_proposals.setdefault(new_index, []).append((score, old_index, signals))
    for values in pair_proposals.values():
        values.sort(key=lambda item: (-item[0], item[1]))

    reciprocal: dict[int, list[tuple[float, int]]] = {}
    for new_index, values in pair_proposals.items():
        for score, old_index, _ in values:
            reciprocal.setdefault(old_index, []).append((score, new_index))
    for values in reciprocal.values():
        values.sort(key=lambda item: (-item[0], item[1]))

    for new_index, values in sorted(pair_proposals.items(), key=lambda item: -item[1][0][0]):
        if new_index in used_new:
            continue
        score, old_index, signals = values[0]
        if old_index in used_old:
            continue
        new_margin = score - values[1][0] if len(values) > 1 else score
        old_values = reciprocal[old_index]
        old_margin = score - old_values[1][0] if len(old_values) > 1 else score
        reciprocal_best = old_values[0][1] == new_index
        composite_evidence = False
        for length in (2, 3, 4):
            for start in range(max(0, new_index - length + 1), min(new_index + 1, len(paragraphs) - length + 1)):
                indexes = range(start, start + length)
                if new_index in indexes and all(index not in used_new for index in indexes):
                    combined = " ".join(paragraphs[index].text for index in indexes)
                    if text_similarity(old[old_index]["text"], combined) >= 0.80:
                        composite_evidence = True
            for start in range(max(0, old_index - length + 1), min(old_index + 1, len(old) - length + 1)):
                indexes = range(start, start + length)
                if old_index in indexes and all(index not in used_old for index in indexes):
                    combined = " ".join(old[index]["text"] for index in indexes)
                    if text_similarity(combined, paragraphs[new_index].text) >= 0.80:
                        composite_evidence = True
        if (
            score >= 0.68 and reciprocal_best and new_margin >= 0.07 and old_margin >= 0.07
            and not composite_evidence
        ):
            assignments[new_index] = {"old_indexes": [old_index], "stable_id": old[old_index]["stable_id"], "kind": "changed"}
            used_new.add(new_index)
            used_old.add(old_index)
            add_group("changed", [old_index], [new_index], score, details=signals)

    def unmatched_new_sequences(length: int) -> list[tuple[int, ...]]:
        return [
            tuple(range(start, start + length))
            for start in range(0, len(paragraphs) - length + 1)
            if all(index not in used_new for index in range(start, start + length))
        ]

    def unmatched_old_sequences(length: int) -> list[tuple[int, ...]]:
        return [
            tuple(range(start, start + length))
            for start in range(0, len(old) - length + 1)
            if all(index not in used_old for index in range(start, start + length))
        ]

    # Detect paragraph splits and merges before declaring additions/removals.
    split_proposals: list[tuple[float, int, tuple[int, ...]]] = []
    for length in (2, 3, 4):
        for new_indexes in unmatched_new_sequences(length):
            combined = " ".join(paragraphs[index].text for index in new_indexes)
            new_token_set = set(tokens(combined))
            for old_index, row in enumerate(old):
                if old_index in used_old:
                    continue
                if not (new_token_set & set(tokens(row["text"]))):
                    continue
                score = text_similarity(row["text"], combined)
                if score >= 0.80:
                    split_proposals.append((score, old_index, new_indexes))
    for score, old_index, new_indexes in sorted(split_proposals, reverse=True):
        if old_index in used_old or any(index in used_new for index in new_indexes):
            continue
        competing = [item for item in split_proposals if item[1] == old_index and item[2] != new_indexes]
        if competing and score - max(item[0] for item in competing) < 0.05:
            continue
        primary = max(new_indexes, key=lambda index: text_similarity(old[old_index]["text"], paragraphs[index].text))
        for new_index in new_indexes:
            assignments[new_index] = {
                "old_indexes": [old_index],
                "stable_id": old[old_index]["stable_id"] if new_index == primary else _uid("p"),
                "kind": "split",
                "primary": new_index == primary,
            }
        used_old.add(old_index)
        used_new.update(new_indexes)
        add_group("split", [old_index], list(new_indexes), score, details={"primary_after_index": primary})

    merge_proposals: list[tuple[float, tuple[int, ...], int]] = []
    for length in (2, 3, 4):
        for old_indexes in unmatched_old_sequences(length):
            combined = " ".join(old[index]["text"] for index in old_indexes)
            old_token_set = set(tokens(combined))
            for new_index, paragraph in enumerate(paragraphs):
                if new_index in used_new or not (old_token_set & set(tokens(paragraph.text))):
                    continue
                score = text_similarity(combined, paragraph.text)
                if score >= 0.80:
                    merge_proposals.append((score, old_indexes, new_index))
    for score, old_indexes, new_index in sorted(merge_proposals, reverse=True):
        if new_index in used_new or any(index in used_old for index in old_indexes):
            continue
        competing = [item for item in merge_proposals if item[2] == new_index and item[1] != old_indexes]
        if competing and score - max(item[0] for item in competing) < 0.05:
            continue
        primary = max(old_indexes, key=lambda index: text_similarity(old[index]["text"], paragraphs[new_index].text))
        assignments[new_index] = {
            "old_indexes": list(old_indexes),
            "stable_id": old[primary]["stable_id"],
            "kind": "merged",
            "primary_old": primary,
        }
        used_old.update(old_indexes)
        used_new.add(new_index)
        add_group("merged", list(old_indexes), [new_index], score, details={"primary_before_index": primary})

    # Remaining plausible links become explicit candidates, never automatic identity.
    candidate_old_indexes: set[int] = set()
    for new_index, paragraph in enumerate(paragraphs):
        if new_index in used_new:
            continue
        chapter_id = chapter_ids[_chapter_for_order(chapters, new_index)]
        candidate_values: list[tuple[float, int, dict]] = []
        exact_old = old_by_fp.get(new_fps[new_index], [])
        indexes = set(exact_old)
        indexes.update(
            _candidate_old_indexes(old, old_token_index, paragraph.text, new_index, chapter_id, len(paragraphs))
        )
        for old_index in indexes:
            if old_index in used_old:
                continue
            score, signals = _pair_score(
                old[old_index], paragraph.text, new_index, new_fps, chapter_id,
                len(paragraphs), len(old),
            )
            if old[old_index]["text_fingerprint"] == new_fps[new_index]:
                score = max(score, 0.76)
                signals["exact_text"] = True
            if score >= 0.44:
                candidate_values.append((score, old_index, signals))
        candidate_values.sort(key=lambda item: (-item[0], item[1]))
        if candidate_values:
            assignments[new_index] = {"old_indexes": [], "stable_id": _uid("p"), "kind": "ambiguous"}
            used_new.add(new_index)
            group = add_group(
                "ambiguous", [], [new_index], candidate_values[0][0],
                status="needs_reconciliation",
                details={"candidate_count": min(5, len(candidate_values))},
            )
            best_old_index = candidate_values[0][1]
            group["semantic_signals"] = semantic_signals(
                [old[best_old_index]["text"]], [paragraph.text]
            )
            group["details"]["semantic_signal_basis"] = "top_unresolved_candidate"
            for rank, (score, old_index, signals) in enumerate(candidate_values[:5], 1):
                candidate_old_indexes.add(old_index)
                signals = {
                    **signals,
                    "lexical_risks": semantic_signals(
                        [old[old_index]["text"]], [paragraph.text]
                    ),
                }
                raw_candidates.append(
                    {
                        "candidate_uid": _uid("cand"),
                        "group_uid": group["group_uid"],
                        "before_indexes": [old_index],
                        "after_indexes": [new_index],
                        "relation_type": "moved" if old[old_index]["text_fingerprint"] == new_fps[new_index] else "changed",
                        "confidence": score,
                        "rank": rank,
                        "signals": signals,
                    }
                )
        else:
            assignments[new_index] = {"old_indexes": [], "stable_id": _uid("p"), "kind": "added"}
            used_new.add(new_index)
            add_group("added", [], [new_index], 1.0)

    for old_index in range(len(old)):
        if old_index not in used_old and old_index not in candidate_old_indexes:
            add_group("removed", [old_index], [], 1.0)

    # A longest-increasing-subsequence pass distinguishes moves from index shifts caused by insertions.
    one_to_one_groups = [
        group for group in raw_groups
        if len(group["before_indexes"]) == len(group["after_indexes"]) == 1
        and group["status"] == "auto"
        and group["relation_type"] in {"unchanged", "changed"}
    ]
    ordered = sorted(
        ((group["after_indexes"][0], group["before_indexes"][0], group) for group in one_to_one_groups),
        key=lambda item: item[0],
    )
    lis_members = _lis_member_positions([(item[0], item[1]) for item in ordered])
    for position, (_, _, group) in enumerate(ordered):
        old_index = group["before_indexes"][0]
        new_index = group["after_indexes"][0]
        current_chapter = chapter_ids[_chapter_for_order(chapters, new_index)]
        moved = position not in lis_members or old[old_index]["chapter_stable_id"] != current_chapter
        if moved:
            group["details"]["moved"] = True
            if group["relation_type"] == "unchanged":
                group["relation_type"] = "moved"

    records: list[dict] = []
    for index, paragraph in enumerate(paragraphs):
        assignment = assignments[index]
        chapter_order = _chapter_for_order(chapters, index)
        records.append(
            {
                "stable_id": assignment["stable_id"],
                "chapter_stable_id": chapter_ids[chapter_order],
                "source_order": index,
                "chapter_order": chapter_order,
                "text": paragraph.text,
                "normalized_text": normalize(paragraph.text),
                "text_fingerprint": new_fps[index],
                "occurrence_index": occurrences[index],
                "context_before": new_fps[index - 1] if index else None,
                "context_after": new_fps[index + 1] if index + 1 < len(new_fps) else None,
                "style_json": paragraph.style,
                "runs_json": paragraph.runs,
                "source_ref_json": paragraph.source_ref,
                "reconciliation_status": assignment["kind"],
            }
        )

    def member(side: str, index: int) -> dict:
        if side == "before":
            row = old[index]
            return {
                "side": side,
                "stable_id": row["stable_id"],
                "paragraph_id": row["id"],
                "source_order": row["source_order"],
                "chapter_stable_id": row["chapter_stable_id"],
                "text": row["text"],
            }
        record = records[index]
        return {
            "side": side,
            "stable_id": record["stable_id"],
            "new_index": index,
            "source_order": record["source_order"],
            "chapter_stable_id": record["chapter_stable_id"],
            "text": record["text"],
        }

    groups: list[dict] = []
    for group in raw_groups:
        groups.append(
            {
                key: value for key, value in group.items()
                if key not in {"before_indexes", "after_indexes"}
            }
            | {
                "members": [member("before", index) for index in group["before_indexes"]]
                + [member("after", index) for index in group["after_indexes"]]
            }
        )
    candidates: list[dict] = []
    for candidate in raw_candidates:
        candidates.append(
            {key: value for key, value in candidate.items() if key not in {"before_indexes", "after_indexes"}}
            | {
                "before_stable_ids": [old[index]["stable_id"] for index in candidate["before_indexes"]],
                "after_stable_ids": [records[index]["stable_id"] for index in candidate["after_indexes"]],
            }
        )

    compatibility: list[dict] = []
    for group in groups:
        before = [item for item in group["members"] if item["side"] == "before"]
        after = [item for item in group["members"] if item["side"] == "after"]
        relation = group["relation_type"]
        if relation in {"unchanged", "moved", "changed"}:
            compatibility.append(
                {
                    "paragraph_stable_id": after[0]["stable_id"],
                    "previous_paragraph_id": before[0]["paragraph_id"],
                    "new_index": after[0]["new_index"],
                    "change_type": "changed" if relation == "changed" else "unchanged",
                    "similarity": group["confidence"],
                    "details": {"relation_type": relation, **group["details"]},
                }
            )
        elif relation == "split":
            primary = next((item for item in after if item["stable_id"] == before[0]["stable_id"]), after[0])
            for item in after:
                compatibility.append(
                    {
                        "paragraph_stable_id": item["stable_id"],
                        "previous_paragraph_id": before[0]["paragraph_id"] if item is primary else None,
                        "new_index": item["new_index"],
                        "change_type": "changed" if item is primary else "added",
                        "similarity": group["confidence"],
                        "details": {"relation_type": "split", "group_uid": group["group_uid"]},
                    }
                )
        elif relation == "merged":
            primary = next((item for item in before if item["stable_id"] == after[0]["stable_id"]), before[0])
            compatibility.append(
                {
                    "paragraph_stable_id": after[0]["stable_id"],
                    "previous_paragraph_id": primary["paragraph_id"],
                    "new_index": after[0]["new_index"],
                    "change_type": "changed",
                    "similarity": group["confidence"],
                    "details": {"relation_type": "merged", "group_uid": group["group_uid"]},
                }
            )
            for item in before:
                if item is not primary:
                    compatibility.append(
                        {
                            "paragraph_stable_id": item["stable_id"],
                            "previous_paragraph_id": item["paragraph_id"],
                            "new_index": None,
                            "change_type": "removed",
                            "similarity": group["confidence"],
                            "details": {"relation_type": "merged", "group_uid": group["group_uid"]},
                        }
                    )
        elif relation == "ambiguous":
            compatibility.append(
                {
                    "paragraph_stable_id": after[0]["stable_id"],
                    "previous_paragraph_id": None,
                    "new_index": after[0]["new_index"],
                    "change_type": "ambiguous",
                    "similarity": group["confidence"],
                    "details": {"candidate_count": group["details"].get("candidate_count", 0)},
                }
            )
        elif relation == "added":
            compatibility.append(
                {
                    "paragraph_stable_id": after[0]["stable_id"], "previous_paragraph_id": None,
                    "new_index": after[0]["new_index"], "change_type": "added",
                    "similarity": None, "details": {"relation_type": "added"},
                }
            )
        elif relation == "removed":
            compatibility.append(
                {
                    "paragraph_stable_id": before[0]["stable_id"],
                    "previous_paragraph_id": before[0]["paragraph_id"], "new_index": None,
                    "change_type": "removed", "similarity": None,
                    "details": {"relation_type": "removed"},
                }
            )

    return RevisionDiffPlan(records, compatibility, groups, candidates)


def plan_chapter_diff(new_chapters: list, chapter_reconciliation: list, old_rows: list) -> tuple[list[dict], list[dict]]:
    old = [dict(row) for row in old_rows]
    old_by_stable = {row["stable_id"]: row for row in old}
    used_old: set[str] = set()
    candidate_old: set[str] = set()
    groups: list[dict] = []
    candidates: list[dict] = []
    for chapter, (stable_id, status) in zip(new_chapters, chapter_reconciliation):
        old_row = old_by_stable.get(stable_id)
        group_uid = _uid("dg")
        after_member = {
            "side": "after", "stable_id": stable_id, "source_order": chapter.source_order,
            "chapter_stable_id": stable_id, "text": chapter.title,
        }
        if old_row:
            used_old.add(stable_id)
            renamed = normalize(old_row["title"]) != normalize(chapter.title)
            moved = old_row["source_order"] != chapter.source_order
            relation = "changed" if renamed else ("moved" if moved else "unchanged")
            confidence = text_similarity(old_row["title"], chapter.title) if renamed else 1.0
            groups.append(
                {
                    "group_uid": group_uid, "entity_type": "chapter", "relation_type": relation,
                    "confidence": confidence, "status": "auto",
                    "semantic_signals": semantic_signals([old_row["title"]], [chapter.title]),
                    "details": {"renamed": renamed, "moved": moved},
                    "members": [
                        {
                            "side": "before", "stable_id": stable_id,
                            "source_order": old_row["source_order"], "chapter_stable_id": stable_id,
                            "text": old_row["title"],
                        },
                        after_member,
                    ],
                }
            )
        elif status == "ambiguous":
            matching = []
            for row in old:
                if row["stable_id"] in used_old:
                    continue
                title_score = text_similarity(row["title"], chapter.title)
                position_score = 1.0 / (1.0 + abs(row["source_order"] - chapter.source_order))
                score = round(title_score * 0.85 + position_score * 0.15, 6)
                if score >= 0.55:
                    matching.append((score, row, title_score, position_score))
            matching.sort(key=lambda item: (-item[0], item[1]["source_order"]))
            top_score = matching[0][0] if matching else 0.0
            top_signals = (
                semantic_signals([matching[0][1]["title"]], [chapter.title])
                if matching else semantic_signals([], [chapter.title])
            )
            groups.append(
                {
                    "group_uid": group_uid, "entity_type": "chapter", "relation_type": "ambiguous",
                    "confidence": top_score, "status": "needs_reconciliation",
                    "semantic_signals": top_signals,
                    "details": {"candidate_count": len(matching)}, "members": [after_member],
                }
            )
            for rank, (score, row, title_score, position_score) in enumerate(matching[:5], 1):
                candidate_old.add(row["stable_id"])
                candidates.append(
                    {
                        "candidate_uid": _uid("cand"), "group_uid": group_uid,
                        "before_stable_ids": [row["stable_id"]], "after_stable_ids": [stable_id],
                        "relation_type": (
                            "moved" if normalize(row["title"]) == normalize(chapter.title)
                            else "changed"
                        ),
                        "confidence": score, "rank": rank,
                        "signals": {
                            "title_similarity": title_score,
                            "position_score": position_score,
                            "lexical_risks": semantic_signals([row["title"]], [chapter.title]),
                        },
                    }
                )
        else:
            groups.append(
                {
                    "group_uid": group_uid, "entity_type": "chapter", "relation_type": "added",
                    "confidence": 1.0, "status": "auto",
                    "semantic_signals": semantic_signals([], [chapter.title]),
                    "details": {}, "members": [after_member],
                }
            )
    for row in old:
        if row["stable_id"] not in used_old and row["stable_id"] not in candidate_old:
            groups.append(
                {
                    "group_uid": _uid("dg"), "entity_type": "chapter", "relation_type": "removed",
                    "confidence": 1.0, "status": "auto",
                    "semantic_signals": semantic_signals([row["title"]], []), "details": {},
                    "members": [
                        {
                            "side": "before", "stable_id": row["stable_id"],
                            "source_order": row["source_order"], "chapter_stable_id": row["stable_id"],
                            "text": row["title"],
                        }
                    ],
                }
            )
    return groups, candidates
