"""Durable, offline manuscript projects and safe structured ingestion.

Manuscript bytes are untrusted data.  This module does not execute document
content, load external resources, or make network requests.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from .revision_diff import confidence_label, plan_chapter_diff, plan_paragraph_diff, text_similarity
from .safe_apply import renderability_errors, render_staged_revision


SCHEMA_VERSION = 6
SUPPORTED_SUFFIXES = {".docx", ".txt", ".md", ".markdown"}
MAX_ARCHIVE_MEMBERS = 5_000
MAX_ARCHIVE_UNCOMPRESSED = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBER = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|(?:chapter|part|book|prologue|epilogue|introduction|conclusion)\b.*|"
    r"(?:глава|часть|книга|пролог|эпилог|введение|заключение)\b.*|"
    r"(?:cap[ií]tulo|parte|pr[oó]logo|ep[ií]logo)\b.*|"
    r"(?:chapitre|partie|prologue|[eé]pilogue)\b.*|"
    r"(?:kapitel|teil|prolog|epilog)\b.*)$",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    return " ".join(text.casefold().split())


def stable_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass
class ExtractedParagraph:
    text: str
    source_order: int
    style: dict = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)
    source_ref: dict = field(default_factory=dict)
    heading: bool = False
    heading_level: int | None = None


@dataclass
class ExtractedChapter:
    title: str
    source_order: int
    start_paragraph: int
    end_paragraph: int


@dataclass
class ExtractedDocument:
    format: str
    paragraphs: list[ExtractedParagraph]
    chapters: list[ExtractedChapter]
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def content_map_hash(self) -> str:
        payload = [
            {
                "order": paragraph.source_order,
                "text": paragraph.text,
                "style": paragraph.style,
                "runs": paragraph.runs,
                "source_ref": paragraph.source_ref,
            }
            for paragraph in self.paragraphs
        ]
        return sha256_bytes(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Text cannot be decoded as UTF-8, UTF-16, or Windows-1252")


def _build_chapters(paragraphs: list[ExtractedParagraph]) -> list[ExtractedChapter]:
    headings = [index for index, paragraph in enumerate(paragraphs) if paragraph.heading]
    if not headings:
        return [ExtractedChapter("Manuscript", 0, 0, len(paragraphs) - 1)]
    chapters: list[ExtractedChapter] = []
    if headings[0] > 0:
        chapters.append(ExtractedChapter("Front matter", 0, 0, headings[0] - 1))
    for heading_position, start in enumerate(headings):
        end = headings[heading_position + 1] - 1 if heading_position + 1 < len(headings) else len(paragraphs) - 1
        title = re.sub(r"^#{1,6}\s+", "", paragraphs[start].text).strip() or "Untitled chapter"
        chapters.append(ExtractedChapter(title, len(chapters), start, end))
    return chapters


def extract_text_document(path: Path, data: bytes) -> ExtractedDocument:
    text, encoding = _decode_text(data)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    paragraphs: list[ExtractedParagraph] = []
    markdown = path.suffix.lower() in {".md", ".markdown"}
    for order, line in enumerate(lines):
        markdown_heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip()) if markdown else None
        heading = bool(markdown_heading or HEADING_RE.match(line.strip()))
        level = len(markdown_heading.group(1)) if markdown_heading else (1 if heading else None)
        paragraphs.append(
            ExtractedParagraph(
                text=line,
                source_order=order,
                style={"kind": "markdown" if markdown else "plain", "heading_level": level},
                runs=[{"text": line}],
                source_ref={"line": order + 1},
                heading=heading,
                heading_level=level,
            )
        )
    if not paragraphs or not any(paragraph.text.strip() for paragraph in paragraphs):
        raise ValueError("The manuscript contains no readable text")
    warnings = []
    if encoding == "cp1252":
        warnings.append("Decoded as Windows-1252; verify characters before editorial use.")
    return ExtractedDocument(
        format="markdown" if markdown else "text",
        paragraphs=paragraphs,
        chapters=_build_chapters(paragraphs),
        warnings=warnings,
        metadata={"encoding": encoding},
    )


def _validate_docx_archive(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"DOCX has too many archive members ({len(members)})")
    total = 0
    for info in members:
        pure = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "\\" in info.filename
            or (pure.parts and ":" in pure.parts[0])
        ):
            raise ValueError(f"Unsafe DOCX archive path: {info.filename}")
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted DOCX members are not supported")
        if stat.S_ISLNK(mode):
            raise ValueError(f"DOCX symlink member is not allowed: {info.filename}")
        if info.file_size > MAX_ARCHIVE_MEMBER:
            raise ValueError(f"DOCX member is unreasonably large: {info.filename}")
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED:
            raise ValueError("DOCX decompression payload exceeds the safety limit")
        if info.file_size and info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO:
            raise ValueError(f"DOCX member compression ratio is unsafe: {info.filename}")


def _safe_xml(data: bytes, label: str) -> ElementTree.Element:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError(f"Unsafe XML declaration in {label}")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError(f"Malformed XML in {label}: {error}") from error


def _docx_styles(archive: zipfile.ZipFile) -> dict[str, dict]:
    try:
        root = _safe_xml(archive.read("word/styles.xml"), "word/styles.xml")
    except KeyError:
        return {}
    styles: dict[str, dict] = {}
    for style in root.findall(f".//{{{W_NS}}}style"):
        style_id = style.attrib.get(f"{{{W_NS}}}styleId", "")
        name_node = style.find(f"{{{W_NS}}}name")
        outline = style.find(f".//{{{W_NS}}}outlineLvl")
        styles[style_id] = {
            "name": name_node.attrib.get(f"{{{W_NS}}}val", "") if name_node is not None else "",
            "outline_level": int(outline.attrib.get(f"{{{W_NS}}}val", "0")) + 1
            if outline is not None
            else None,
        }
    return styles


def _run_payload(run: ElementTree.Element) -> dict:
    parts: list[str] = []
    for node in run.iter():
        if node.tag == f"{{{W_NS}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{W_NS}}}tab":
            parts.append("\t")
        elif node.tag in {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}:
            parts.append("\n")
    properties = run.find(f"{{{W_NS}}}rPr")
    payload: dict = {"text": "".join(parts)}
    if properties is not None:
        payload.update(
            {
                "bold": properties.find(f"{{{W_NS}}}b") is not None,
                "italic": properties.find(f"{{{W_NS}}}i") is not None,
                "underline": properties.find(f"{{{W_NS}}}u") is not None,
            }
        )
        style_node = properties.find(f"{{{W_NS}}}rStyle")
        if style_node is not None:
            payload["style_id"] = style_node.attrib.get(f"{{{W_NS}}}val", "")
    return payload


def _paragraph_runs(paragraph: ElementTree.Element) -> list[ElementTree.Element]:
    runs: list[ElementTree.Element] = []

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag == f"{{{W_NS}}}p":
                continue
            if child.tag == f"{{{W_NS}}}r":
                runs.append(child)
            else:
                visit(child)

    visit(paragraph)
    return runs


def extract_docx(path: Path) -> ExtractedDocument:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ValueError(f"Invalid DOCX archive: {error}") from error
    with archive:
        _validate_docx_archive(archive)
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX is missing word/document.xml")
        root = _safe_xml(archive.read("word/document.xml"), "word/document.xml")
        styles = _docx_styles(archive)
        paragraphs: list[ExtractedParagraph] = []
        fallback_headings = 0
        for order, paragraph in enumerate(root.iter(f"{{{W_NS}}}p")):
            runs = [_run_payload(run) for run in _paragraph_runs(paragraph)]
            text = "".join(run["text"] for run in runs)
            properties = paragraph.find(f"{{{W_NS}}}pPr")
            style_id = ""
            numbering: dict = {}
            if properties is not None:
                style_node = properties.find(f"{{{W_NS}}}pStyle")
                if style_node is not None:
                    style_id = style_node.attrib.get(f"{{{W_NS}}}val", "")
                number_node = properties.find(f"{{{W_NS}}}numPr")
                if number_node is not None:
                    for key in ("numId", "ilvl"):
                        node = number_node.find(f"{{{W_NS}}}{key}")
                        if node is not None:
                            numbering[key] = node.attrib.get(f"{{{W_NS}}}val", "")
            style_info = styles.get(style_id, {})
            style_name = style_info.get("name", "")
            heading_level = style_info.get("outline_level")
            style_heading = bool(
                heading_level
                or style_id.casefold().startswith(("heading", "title"))
                or style_name.casefold().startswith(("heading", "title"))
            )
            text_heading = bool(text.strip() and HEADING_RE.match(text.strip()))
            if text_heading and not style_heading:
                fallback_headings += 1
            paragraphs.append(
                ExtractedParagraph(
                    text=text,
                    source_order=order,
                    style={
                        "style_id": style_id,
                        "style_name": style_name,
                        "numbering": numbering,
                    },
                    runs=runs,
                    source_ref={"part": "word/document.xml", "paragraph_index": order},
                    heading=style_heading or text_heading,
                    heading_level=heading_level or (1 if style_heading or text_heading else None),
                )
            )
        if not paragraphs or not any(paragraph.text.strip() for paragraph in paragraphs):
            raise ValueError("The DOCX contains no readable body paragraphs")
        warnings: list[str] = []
        if fallback_headings:
            warnings.append(
                f"Detected {fallback_headings} heading(s) from text because Word heading styles were absent or inconsistent."
            )
        unsupported_parts = sorted(
            name
            for name in names
            if re.match(r"word/(?:footnotes|endnotes|comments|header\d+|footer\d+)\.xml$", name)
        )
        if unsupported_parts:
            warnings.append("Content in these auxiliary Word parts is not extracted: " + ", ".join(unsupported_parts))
        media = [name for name in names if name.startswith("word/media/") and not name.endswith("/")]
        if media:
            warnings.append(f"Preserved {len(media)} embedded media file(s) only in the immutable DOCX snapshot.")
        if root.find(f".//{{{W_NS}}}tbl") is not None:
            warnings.append("Table paragraphs were extracted in document order; table geometry is not preserved yet.")
        if root.find(f".//{{{W_NS}}}txbxContent") is not None:
            warnings.append("Text-box paragraphs were extracted, but text-box positioning is not preserved yet.")
        if root.find(f".//{{{W_NS}}}ins") is not None or root.find(f".//{{{W_NS}}}del") is not None:
            warnings.append("Tracked-change markup is not modeled; review the immutable DOCX snapshot before use.")
        return ExtractedDocument(
            format="docx",
            paragraphs=paragraphs,
            chapters=_build_chapters(paragraphs),
            warnings=warnings,
            metadata={"styles_part": "word/styles.xml" in names, "archive_members": len(names)},
        )


def extract_manuscript(path: Path) -> tuple[ExtractedDocument, str, int]:
    source = path.resolve()
    if not source.is_file():
        raise ValueError(f"Source file not found: {source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("Supported inputs are DOCX, TXT, and Markdown")
    source_hash = sha256_file(source)
    size = source.stat().st_size
    if suffix == ".docx":
        document = extract_docx(source)
    else:
        document = extract_text_document(source, source.read_bytes())
    return document, source_hash, size


MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations(
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects(
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT,
    author TEXT,
    language TEXT,
    metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manuscript_revisions(
    id INTEGER PRIMARY KEY,
    revision_uid TEXT NOT NULL UNIQUE,
    parent_revision_uid TEXT REFERENCES manuscript_revisions(revision_uid),
    source_name TEXT NOT NULL,
    source_format TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    snapshot_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    title TEXT,
    author TEXT,
    language TEXT,
    content_map_hash TEXT NOT NULL,
    paragraph_count INTEGER NOT NULL,
    chapter_count INTEGER NOT NULL,
    warnings_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    legacy_revision_id INTEGER UNIQUE,
    status TEXT NOT NULL DEFAULT 'ready'
);
CREATE TABLE IF NOT EXISTS chapters(
    id INTEGER PRIMARY KEY,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    stable_id TEXT NOT NULL,
    source_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_paragraph INTEGER NOT NULL,
    end_paragraph INTEGER NOT NULL,
    reconciliation_status TEXT NOT NULL,
    UNIQUE(revision_uid, source_order),
    UNIQUE(revision_uid, stable_id)
);
CREATE TABLE IF NOT EXISTS paragraphs(
    id INTEGER PRIMARY KEY,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    chapter_stable_id TEXT NOT NULL,
    stable_id TEXT NOT NULL,
    source_order INTEGER NOT NULL,
    chapter_order INTEGER NOT NULL,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    text_fingerprint TEXT NOT NULL,
    occurrence_index INTEGER NOT NULL,
    context_before TEXT,
    context_after TEXT,
    style_json TEXT NOT NULL,
    runs_json TEXT NOT NULL,
    source_ref_json TEXT NOT NULL,
    reconciliation_status TEXT NOT NULL,
    UNIQUE(revision_uid, source_order),
    UNIQUE(revision_uid, stable_id)
);
CREATE TABLE IF NOT EXISTS paragraph_changes(
    id INTEGER PRIMARY KEY,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    paragraph_stable_id TEXT,
    previous_paragraph_id INTEGER,
    current_paragraph_id INTEGER,
    change_type TEXT NOT NULL CHECK(change_type IN ('unchanged','changed','added','removed','ambiguous')),
    similarity REAL,
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editorial_findings(
    finding_id TEXT PRIMARY KEY,
    source_revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    paragraph_stable_id TEXT,
    anchor_json TEXT NOT NULL,
    exact_quote TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    legacy_finding_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS author_decisions(
    decision_id TEXT PRIMARY KEY,
    decision_type TEXT NOT NULL,
    source_revision_uid TEXT,
    source_fragment_id TEXT,
    payload_json TEXT NOT NULL,
    selected_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','approved','rejected','deferred')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    legacy_decision_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS decision_events(
    id INTEGER PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES author_decisions(decision_id),
    status TEXT NOT NULL,
    selected_json TEXT,
    actor TEXT NOT NULL,
    rationale TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
    run_id TEXT PRIMARY KEY,
    revision_uid TEXT REFERENCES manuscript_revisions(revision_uid),
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    revision_uid TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paragraphs_revision ON paragraphs(revision_uid, source_order);
CREATE INDEX IF NOT EXISTS idx_paragraphs_fingerprint ON paragraphs(revision_uid, text_fingerprint);
CREATE INDEX IF NOT EXISTS idx_findings_revision ON editorial_findings(source_revision_uid);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS revision_diff_groups(
    group_uid TEXT PRIMARY KEY,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    parent_revision_uid TEXT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('chapter','paragraph')),
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    semantic_signals_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revision_diff_members(
    id INTEGER PRIMARY KEY,
    group_uid TEXT NOT NULL REFERENCES revision_diff_groups(group_uid),
    side TEXT NOT NULL CHECK(side IN ('before','after')),
    entity_stable_id TEXT NOT NULL,
    paragraph_id INTEGER,
    source_order INTEGER NOT NULL,
    chapter_stable_id TEXT,
    text_snapshot TEXT NOT NULL,
    UNIQUE(group_uid,side,entity_stable_id)
);
CREATE TABLE IF NOT EXISTS reconciliation_candidates(
    candidate_uid TEXT PRIMARY KEY,
    group_uid TEXT NOT NULL REFERENCES revision_diff_groups(group_uid),
    before_stable_ids_json TEXT NOT NULL,
    after_stable_ids_json TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    rank INTEGER NOT NULL,
    signals_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_events(
    sequence INTEGER PRIMARY KEY,
    event_uid TEXT NOT NULL UNIQUE,
    group_uid TEXT NOT NULL REFERENCES revision_diff_groups(group_uid),
    action TEXT NOT NULL CHECK(action IN ('resolve','reopen')),
    relation_type TEXT,
    before_stable_ids_json TEXT NOT NULL,
    after_stable_ids_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    supersedes_event_uid TEXT REFERENCES reconciliation_events(event_uid)
);
CREATE INDEX IF NOT EXISTS idx_diff_groups_revision ON revision_diff_groups(revision_uid,entity_type);
CREATE INDEX IF NOT EXISTS idx_diff_members_group ON revision_diff_members(group_uid,side);
CREATE INDEX IF NOT EXISTS idx_candidates_group ON reconciliation_candidates(group_uid,rank);
CREATE INDEX IF NOT EXISTS idx_reconciliation_events_group ON reconciliation_events(group_uid,sequence);
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS corrections(
    correction_id TEXT PRIMARY KEY,
    source_revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    paragraph_stable_id TEXT NOT NULL,
    finding_id TEXT REFERENCES editorial_findings(finding_id),
    group_uid TEXT,
    exact_before TEXT NOT NULL,
    replacement TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    change_class TEXT NOT NULL CHECK(change_class IN ('mechanical','semantic')),
    category TEXT,
    rationale TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','deferred','applied')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correction_events(
    sequence INTEGER PRIMARY KEY,
    event_uid TEXT NOT NULL UNIQUE,
    correction_id TEXT NOT NULL REFERENCES corrections(correction_id),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','deferred','applied')),
    actor TEXT NOT NULL,
    rationale TEXT,
    batch_uid TEXT,
    source_revision_uid TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_applications(
    application_uid TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK(operation IN ('apply','rollback')),
    source_revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    result_revision_uid TEXT NOT NULL UNIQUE REFERENCES manuscript_revisions(revision_uid),
    rollback_of_uid TEXT REFERENCES change_applications(application_uid),
    plan_hash TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    correction_ids_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_revision ON corrections(source_revision_uid,status);
CREATE INDEX IF NOT EXISTS idx_correction_events_id ON correction_events(correction_id,sequence);
CREATE INDEX IF NOT EXISTS idx_applications_source ON change_applications(source_revision_uid,created_at);
"""

MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS run_units(
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    unit_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('chapter','paragraph')),
    source_stable_id TEXT NOT NULL,
    source_order INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','in_progress','completed','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    active_attempt_uid TEXT,
    worker_id TEXT,
    result_path TEXT,
    result_sha256 TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,unit_id)
);
CREATE TABLE IF NOT EXISTS run_events(
    sequence INTEGER PRIMARY KEY,
    event_uid TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    unit_id TEXT,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    attempt_uid TEXT,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_units_status ON run_units(run_id,status,source_order);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id,sequence);
"""

MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS book_maps(
    map_run_id TEXT PRIMARY KEY,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    schema_version INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    pass_a_run_id TEXT NOT NULL REFERENCES runs(run_id),
    pass_b_run_id TEXT NOT NULL REFERENCES runs(run_id),
    status TEXT NOT NULL CHECK(status IN ('validated')),
    coverage_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS book_map_records(
    map_run_id TEXT NOT NULL REFERENCES book_maps(map_run_id),
    record_id TEXT NOT NULL,
    source_pass TEXT NOT NULL CHECK(source_pass IN ('A','B')),
    source_unit_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    record_class TEXT NOT NULL CHECK(record_class IN ('observed_fact','editorial_hypothesis','author_decision')),
    epistemic_status TEXT NOT NULL,
    scope TEXT NOT NULL,
    narrative_frame TEXT NOT NULL,
    statement TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(map_run_id,record_id)
);
CREATE TABLE IF NOT EXISTS book_map_evidence(
    map_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    evidence_order INTEGER NOT NULL,
    revision_uid TEXT NOT NULL REFERENCES manuscript_revisions(revision_uid),
    paragraph_stable_id TEXT NOT NULL,
    exact_quote TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    PRIMARY KEY(map_run_id,record_id,evidence_order),
    FOREIGN KEY(map_run_id,record_id)
        REFERENCES book_map_records(map_run_id,record_id)
);
CREATE INDEX IF NOT EXISTS idx_book_maps_revision ON book_maps(revision_uid,created_at);
CREATE INDEX IF NOT EXISTS idx_book_map_records_kind ON book_map_records(map_run_id,kind);
CREATE INDEX IF NOT EXISTS idx_book_map_evidence_paragraph
    ON book_map_evidence(revision_uid,paragraph_stable_id);
"""


def _table_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}


def _safe_project_relative(value: str) -> Path | None:
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
        return None
    return Path(*pure.parts)


def _apply_migrations(connection: sqlite3.Connection, root: Path) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(MIGRATION_1)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(1,?,?)",
        ("durable manuscript state", utc_now()),
    )
    migrated = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=2"
    ).fetchone()
    if not migrated:
        _migrate_preview_ledger(connection, root)
        connection.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES(2,?,?)",
            ("preserve preview ledger", utc_now()),
        )
    migrated_diff = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=3"
    ).fetchone()
    if migrated_diff:
        connection.executescript(MIGRATION_3)
    else:
        connection.executescript("BEGIN;\n" + MIGRATION_3)
        try:
            _backfill_revision_diffs(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(3,?,?)",
                ("structural revision diff and reconciliation", utc_now()),
            )
        except Exception:
            connection.rollback()
            raise
    migrated_apply = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=4"
    ).fetchone()
    if migrated_apply:
        connection.executescript(MIGRATION_4)
    else:
        connection.executescript("BEGIN;\n" + MIGRATION_4)
        try:
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(4,?,?)",
                ("author-approved safe correction application", utc_now()),
            )
        except Exception:
            connection.rollback()
            raise
    migrated_runs = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=5"
    ).fetchone()
    if migrated_runs:
        connection.executescript(MIGRATION_5)
    else:
        connection.executescript("BEGIN;\n" + MIGRATION_5)
        try:
            connection.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(5,?,?)",
                ("resumable run checkpoints and coverage", utc_now()),
            )
        except Exception:
            connection.rollback()
            raise
    migrated_book_maps = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=6"
    ).fetchone()
    if migrated_book_maps:
        connection.executescript(MIGRATION_6)
        return
    connection.executescript("BEGIN;\n" + MIGRATION_6)
    try:
        connection.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES(6,?,?)",
            ("whole-book map canonical records", utc_now()),
        )
    except Exception:
        connection.rollback()
        raise


def _migrate_preview_ledger(connection: sqlite3.Connection, root: Path) -> None:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    now = utc_now()
    legacy_to_uid: dict[int, str] = {}
    if "revisions" in tables and {"id", "parent", "filename", "sha256"} <= _table_columns(connection, "revisions"):
        for row in connection.execute(
            "SELECT id,parent,filename,sha256,created,note FROM revisions ORDER BY id"
        ):
            legacy_id, parent, filename, checksum, created, note = row
            uid = f"legacy-r{legacy_id:06d}"
            legacy_to_uid[legacy_id] = uid
            relative_snapshot = _safe_project_relative(filename)
            snapshot = root / relative_snapshot if relative_snapshot else root / "revisions" / f"missing-legacy-{legacy_id}"
            connection.execute(
                """INSERT OR IGNORE INTO manuscript_revisions(
                    revision_uid,parent_revision_uid,source_name,source_format,source_sha256,
                    source_size,snapshot_path,created_at,content_map_hash,paragraph_count,
                    chapter_count,warnings_json,metadata_json,legacy_revision_id,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uid,
                    f"legacy-r{parent:06d}" if parent else None,
                    Path(filename).name,
                    Path(filename).suffix.lower().lstrip(".") or "unknown",
                    checksum,
                    snapshot.stat().st_size if snapshot.is_file() else 0,
                    relative_snapshot.as_posix() if relative_snapshot else f"revisions/missing-legacy-{legacy_id}",
                    created,
                    "",
                    0,
                    0,
                    json.dumps([
                        "Migrated from preview ledger; structured extraction unavailable.",
                        *(["Unsafe legacy snapshot path was not followed."] if relative_snapshot is None else []),
                    ]),
                    json.dumps({"legacy_note": note}),
                    legacy_id,
                    "legacy",
                ),
            )
    if "findings" in tables and {"id", "revision_id", "anchor"} <= _table_columns(connection, "findings"):
        for row in connection.execute(
            "SELECT id,revision_id,anchor,quote,recommendation,status,created FROM findings"
        ):
            finding_id, revision_id, anchor, quote, recommendation, status, created = row
            uid = legacy_to_uid.get(revision_id)
            if uid:
                connection.execute(
                    """INSERT OR IGNORE INTO editorial_findings VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        f"legacy:{finding_id}", uid, None,
                        json.dumps({"legacy_anchor": anchor}), quote, recommendation,
                        status, created, finding_id,
                    ),
                )
    if "decisions" in tables and {"id", "kind", "status"} <= _table_columns(connection, "decisions"):
        for row in connection.execute(
            "SELECT id,kind,source,context,alternatives,selected,status,created,updated FROM decisions"
        ):
            decision_id, kind, source, context, alternatives, selected, status, created, updated = row
            canonical_status = "approved" if status == "approved" else status
            new_id = f"legacy:{decision_id}"
            connection.execute(
                """INSERT OR IGNORE INTO author_decisions VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id, kind, None, None,
                    json.dumps({"source": source, "context": context, "alternatives": json.loads(alternatives)}),
                    json.dumps(selected) if selected is not None else None,
                    canonical_status, created, updated, decision_id,
                ),
            )
            connection.execute(
                """INSERT INTO decision_events(decision_id,status,selected_json,actor,rationale,created_at)
                   SELECT ?,?,?,?,?,? WHERE NOT EXISTS(
                       SELECT 1 FROM decision_events WHERE decision_id=?
                   )""",
                (new_id, canonical_status, json.dumps(selected) if selected else None,
                 "preview-ledger-migration", "Imported without changing the decision", updated, new_id),
            )
    connection.execute(
        "INSERT INTO audit_log(event_type,payload_json,created_at) VALUES(?,?,?)",
        ("schema.migrated", json.dumps({"from": "preview-ledger", "to": SCHEMA_VERSION}), now),
    )


def _backfill_revision_diffs(connection: sqlite3.Connection) -> None:
    """Expose PR-02 change rows through the v3 structured-diff API without rewriting them."""
    revisions = connection.execute(
        "SELECT revision_uid,parent_revision_uid,created_at FROM manuscript_revisions ORDER BY id"
    ).fetchall()
    for revision in revisions:
        revision_uid = revision["revision_uid"]
        if connection.execute(
            "SELECT 1 FROM revision_diff_groups WHERE revision_uid=? LIMIT 1", (revision_uid,)
        ).fetchone():
            continue
        for change in connection.execute(
            "SELECT * FROM paragraph_changes WHERE revision_uid=? ORDER BY id", (revision_uid,)
        ):
            try:
                details = json.loads(change["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {"legacy_details_invalid": True}
            relation = details.get("relation_type", change["change_type"])
            group_uid = f"legacy-dg-{change['id']}"
            connection.execute(
                """INSERT INTO revision_diff_groups VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    group_uid, revision_uid, revision["parent_revision_uid"], "paragraph",
                    relation, change["similarity"] if change["similarity"] is not None else 1.0,
                    "needs_reconciliation" if change["change_type"] == "ambiguous" else "legacy_auto",
                    json.dumps({"semantic_interpretation": "not_evaluated", "legacy_backfill": True}),
                    json.dumps(details, ensure_ascii=False, sort_keys=True), revision["created_at"],
                ),
            )
            for side, paragraph_id in (
                ("before", change["previous_paragraph_id"]),
                ("after", change["current_paragraph_id"]),
            ):
                if paragraph_id is None:
                    continue
                paragraph = connection.execute(
                    "SELECT * FROM paragraphs WHERE id=?", (paragraph_id,)
                ).fetchone()
                if paragraph:
                    connection.execute(
                        """INSERT INTO revision_diff_members(
                            group_uid,side,entity_stable_id,paragraph_id,source_order,
                            chapter_stable_id,text_snapshot
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            group_uid, side, paragraph["stable_id"], paragraph["id"],
                            paragraph["source_order"], paragraph["chapter_stable_id"], paragraph["text"],
                        ),
                    )
        if revision["parent_revision_uid"]:
            represented_before = {
                row[0] for row in connection.execute(
                    """SELECT m.entity_stable_id
                       FROM revision_diff_members m
                       JOIN revision_diff_groups g ON g.group_uid=m.group_uid
                       WHERE g.revision_uid=? AND g.entity_type='paragraph' AND m.side='before'""",
                    (revision_uid,),
                )
            }
            for paragraph in connection.execute(
                "SELECT * FROM paragraphs WHERE revision_uid=? ORDER BY source_order",
                (revision["parent_revision_uid"],),
            ).fetchall():
                if paragraph["stable_id"] in represented_before:
                    continue
                group_uid = f"legacy-dg-unresolved-{revision_uid}-{paragraph['id']}"
                connection.execute(
                    """INSERT INTO revision_diff_groups VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        group_uid, revision_uid, revision["parent_revision_uid"], "paragraph",
                        "ambiguous", 0.0, "needs_reconciliation",
                        json.dumps({
                            "semantic_interpretation": "not_evaluated",
                            "legacy_backfill": True,
                        }),
                        json.dumps({"legacy_unmatched_before": True}), revision["created_at"],
                    ),
                )
                connection.execute(
                    """INSERT INTO revision_diff_members(
                        group_uid,side,entity_stable_id,paragraph_id,source_order,
                        chapter_stable_id,text_snapshot
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        group_uid, "before", paragraph["stable_id"], paragraph["id"],
                        paragraph["source_order"], paragraph["chapter_stable_id"], paragraph["text"],
                    ),
                )
        current_chapters = connection.execute(
            "SELECT * FROM chapters WHERE revision_uid=? ORDER BY source_order", (revision_uid,)
        ).fetchall()
        previous_by_stable = {
            row["stable_id"]: row
            for row in connection.execute(
                "SELECT * FROM chapters WHERE revision_uid=?", (revision["parent_revision_uid"],)
            )
        } if revision["parent_revision_uid"] else {}
        seen_previous: set[str] = set()
        for chapter in current_chapters:
            previous = previous_by_stable.get(chapter["stable_id"])
            relation = "added"
            if previous:
                seen_previous.add(previous["stable_id"])
                relation = "changed" if previous["title"] != chapter["title"] else (
                    "moved" if previous["source_order"] != chapter["source_order"] else "unchanged"
                )
            group_uid = f"legacy-ch-{revision_uid}-{chapter['id']}"
            connection.execute(
                "INSERT INTO revision_diff_groups VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    group_uid, revision_uid, revision["parent_revision_uid"], "chapter", relation,
                    1.0, "legacy_auto",
                    json.dumps({"semantic_interpretation": "not_evaluated", "legacy_backfill": True}),
                    "{}", revision["created_at"],
                ),
            )
            if previous:
                connection.execute(
                    """INSERT INTO revision_diff_members(
                        group_uid,side,entity_stable_id,paragraph_id,source_order,chapter_stable_id,text_snapshot
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (group_uid, "before", previous["stable_id"], None, previous["source_order"], previous["stable_id"], previous["title"]),
                )
            connection.execute(
                """INSERT INTO revision_diff_members(
                    group_uid,side,entity_stable_id,paragraph_id,source_order,chapter_stable_id,text_snapshot
                ) VALUES(?,?,?,?,?,?,?)""",
                (group_uid, "after", chapter["stable_id"], None, chapter["source_order"], chapter["stable_id"], chapter["title"]),
            )
        for previous in previous_by_stable.values():
            if previous["stable_id"] in seen_previous:
                continue
            group_uid = f"legacy-ch-removed-{revision_uid}-{previous['id']}"
            connection.execute(
                "INSERT INTO revision_diff_groups VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    group_uid, revision_uid, revision["parent_revision_uid"], "chapter", "removed",
                    1.0, "legacy_auto",
                    json.dumps({"semantic_interpretation": "not_evaluated", "legacy_backfill": True}),
                    "{}", revision["created_at"],
                ),
            )
            connection.execute(
                """INSERT INTO revision_diff_members(
                    group_uid,side,entity_stable_id,paragraph_id,source_order,chapter_stable_id,text_snapshot
                ) VALUES(?,?,?,?,?,?,?)""",
                (group_uid, "before", previous["stable_id"], None, previous["source_order"], previous["stable_id"], previous["title"]),
            )


def _reconcile_chapters(
    chapters: list[ExtractedChapter], old_rows: list[sqlite3.Row]
) -> list[tuple[str, str]]:
    results: list[tuple[str, str] | None] = [None] * len(chapters)
    available_old = set(range(len(old_rows)))
    available_new = set(range(len(chapters)))
    old_by_title: dict[str, list[int]] = {}
    new_by_title: dict[str, list[int]] = {}
    for index, row in enumerate(old_rows):
        old_by_title.setdefault(normalized_text(row["title"]), []).append(index)
    for index, chapter in enumerate(chapters):
        new_by_title.setdefault(normalized_text(chapter.title), []).append(index)

    # Reserve exact chapter identities globally before considering positional renames.
    for title, new_indexes in new_by_title.items():
        old_indexes = old_by_title.get(title, [])
        if old_indexes and len(old_indexes) == len(new_indexes):
            for new_index, old_index in zip(new_indexes, old_indexes):
                results[new_index] = (old_rows[old_index]["stable_id"], "matched")
                available_new.discard(new_index)
                available_old.discard(old_index)

    proposals: dict[int, list[tuple[float, int]]] = {}
    for new_index in available_new:
        for old_index in available_old:
            title_score = text_similarity(old_rows[old_index]["title"], chapters[new_index].title)
            position_score = 1.0 / (1.0 + abs(old_rows[old_index]["source_order"] - chapters[new_index].source_order))
            score = title_score * 0.85 + position_score * 0.15
            if score >= 0.55:
                proposals.setdefault(new_index, []).append((score, old_index))
    for values in proposals.values():
        values.sort(reverse=True)
    reciprocal: dict[int, list[tuple[float, int]]] = {}
    for new_index, values in proposals.items():
        for score, old_index in values:
            reciprocal.setdefault(old_index, []).append((score, new_index))
    for values in reciprocal.values():
        values.sort(reverse=True)
    for new_index, values in sorted(proposals.items(), key=lambda item: -item[1][0][0]):
        if new_index not in available_new:
            continue
        score, old_index = values[0]
        if old_index not in available_old:
            continue
        new_margin = score - values[1][0] if len(values) > 1 else score
        old_values = reciprocal[old_index]
        old_margin = score - old_values[1][0] if len(old_values) > 1 else score
        if old_values[0][1] == new_index and score >= 0.62 and new_margin >= 0.08 and old_margin >= 0.08:
            results[new_index] = (old_rows[old_index]["stable_id"], "changed")
            available_new.remove(new_index)
            available_old.remove(old_index)

    for new_index in available_new:
        values = proposals.get(new_index, [])
        if values and values[0][0] >= 0.55:
            results[new_index] = (stable_id("ch"), "ambiguous")
        else:
            results[new_index] = (stable_id("ch"), "new")
    return [result for result in results if result is not None]


class ManuscriptProject:
    def __init__(self, root: Path, connection: sqlite3.Connection):
        self.root = root
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        title: str | None = None,
        author: str | None = None,
        language: str | None = None,
    ) -> "ManuscriptProject":
        root = Path(path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        project_file = root / "project.json"
        database_file = root / "editroast.sqlite"
        if project_file.exists() or database_file.exists():
            raise ValueError(f"An EditRoast project already exists at {root}")
        for folder in ("revisions", ".staging", "work", "reports"):
            (root / folder).mkdir(exist_ok=True)
        connection = sqlite3.connect(database_file)
        project = cls(root, connection)
        try:
            with connection:
                _apply_migrations(connection, root)
                project_id = stable_id("project")
                now = utc_now()
                connection.execute(
                    "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
                    (project_id, now, now, title, author, language, "{}"),
                )
                connection.execute(
                    "INSERT INTO audit_log(event_type,payload_json,created_at) VALUES(?,?,?)",
                    ("project.created", json.dumps({"project_id": project_id}), now),
                )
            atomic_json(
                project_file,
                {
                    "schema_version": SCHEMA_VERSION,
                    "project_id": project_id,
                    "created_at": now,
                    "title": title,
                    "author": author,
                    "language": language,
                },
            )
            return project
        except Exception:
            connection.close()
            raise

    @classmethod
    def open(cls, path: str | Path) -> "ManuscriptProject":
        root = Path(path).resolve()
        database_file = root / "editroast.sqlite"
        if not database_file.is_file():
            raise ValueError(f"EditRoast database not found: {database_file}")
        connection = sqlite3.connect(database_file)
        project = cls(root, connection)
        try:
            with connection:
                _apply_migrations(connection, root)
                if not connection.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
                    now = utc_now()
                    project_id = stable_id("project")
                    connection.execute(
                        "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
                        (project_id, now, now, None, None, None, json.dumps({"migrated_preview": True})),
                    )
                    if not (root / "project.json").exists():
                        atomic_json(
                            root / "project.json",
                            {"schema_version": SCHEMA_VERSION, "project_id": project_id, "created_at": now},
                        )
            project_file = root / "project.json"
            if not project_file.exists():
                row = connection.execute("SELECT * FROM projects LIMIT 1").fetchone()
                atomic_json(
                    project_file,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "project_id": row["id"],
                        "created_at": row["created_at"],
                        "title": row["title"],
                        "author": row["author"],
                        "language": row["language"],
                    },
                )
            project._recover_incomplete_files()
            return project
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ManuscriptProject":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _recover_incomplete_files(self) -> None:
        staging = self.root / ".staging"
        staging.mkdir(exist_ok=True)
        for path in staging.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        known = {
            row[0]
            for row in self.connection.execute("SELECT snapshot_path FROM manuscript_revisions")
        }
        revisions = self.root / "revisions"
        revisions.mkdir(exist_ok=True)
        for child in revisions.iterdir():
            if not child.is_dir():
                continue
            contained = [path.relative_to(self.root).as_posix() for path in child.rglob("*") if path.is_file()]
            if contained and not any(path in known for path in contained):
                shutil.rmtree(child)

    def _store_revision_diff(
        self,
        revision_uid: str,
        parent_uid: str | None,
        groups: list[dict],
        candidates: list[dict],
        current_ids: dict[int, int],
        created_at: str,
    ) -> None:
        for group in groups:
            self.connection.execute(
                """INSERT INTO revision_diff_groups(
                    group_uid,revision_uid,parent_revision_uid,entity_type,relation_type,
                    confidence,status,semantic_signals_json,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    group["group_uid"], revision_uid, parent_uid, group["entity_type"],
                    group["relation_type"], group["confidence"], group["status"],
                    json.dumps(group["semantic_signals"], ensure_ascii=False, sort_keys=True),
                    json.dumps(group["details"], ensure_ascii=False, sort_keys=True), created_at,
                ),
            )
            for member in group["members"]:
                paragraph_id = member.get("paragraph_id")
                if member["side"] == "after" and "new_index" in member:
                    paragraph_id = current_ids[member["new_index"]]
                self.connection.execute(
                    """INSERT INTO revision_diff_members(
                        group_uid,side,entity_stable_id,paragraph_id,source_order,
                        chapter_stable_id,text_snapshot
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        group["group_uid"], member["side"], member["stable_id"], paragraph_id,
                        member["source_order"], member.get("chapter_stable_id"), member["text"],
                    ),
                )
        for candidate in candidates:
            self.connection.execute(
                """INSERT INTO reconciliation_candidates(
                    candidate_uid,group_uid,before_stable_ids_json,after_stable_ids_json,
                    relation_type,confidence,rank,signals_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    candidate["candidate_uid"], candidate["group_uid"],
                    json.dumps(candidate["before_stable_ids"], ensure_ascii=False),
                    json.dumps(candidate["after_stable_ids"], ensure_ascii=False),
                    candidate["relation_type"], candidate["confidence"], candidate["rank"],
                    json.dumps(candidate["signals"], ensure_ascii=False, sort_keys=True),
                ),
            )

    def import_manuscript(
        self,
        source: str | Path,
        *,
        title: str | None = None,
        author: str | None = None,
        language: str | None = None,
        _source_name: str | None = None,
        _revision_metadata: dict | None = None,
        _transaction_hook=None,
        _expected_parent_uid: str | None = None,
    ) -> dict:
        source_path = Path(source).resolve()
        document, source_hash, source_size = extract_manuscript(source_path)
        project_values = self.connection.execute(
            "SELECT title,author,language FROM projects LIMIT 1"
        ).fetchone()
        resolved_title = title if title is not None else project_values[0]
        resolved_author = author if author is not None else project_values[1]
        resolved_language = language if language is not None else project_values[2]
        latest = self.connection.execute(
            "SELECT revision_uid FROM manuscript_revisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        parent_uid = latest[0] if latest else None
        if _expected_parent_uid is not None and parent_uid != _expected_parent_uid:
            raise ValueError(
                f"Source revision is stale: expected latest {_expected_parent_uid}, found {parent_uid}"
            )
        old_chapters = list(
            self.connection.execute(
                "SELECT * FROM chapters WHERE revision_uid=? ORDER BY source_order", (parent_uid,)
            )
        ) if parent_uid else []
        old_paragraphs = list(
            self.connection.execute(
                "SELECT * FROM paragraphs WHERE revision_uid=? ORDER BY source_order", (parent_uid,)
            )
        ) if parent_uid else []
        chapter_reconciliation = _reconcile_chapters(document.chapters, old_chapters)
        chapter_ids = [item[0] for item in chapter_reconciliation]
        paragraph_plan = plan_paragraph_diff(
            document.paragraphs, chapter_ids, document.chapters, old_paragraphs
        )
        chapter_groups, chapter_candidates = plan_chapter_diff(
            document.chapters, chapter_reconciliation, old_chapters
        )
        paragraph_records = paragraph_plan.paragraph_records
        changes = paragraph_plan.compatibility_changes
        diff_groups = chapter_groups + paragraph_plan.groups
        diff_candidates = chapter_candidates + paragraph_plan.candidates
        next_id = self.connection.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM manuscript_revisions"
        ).fetchone()[0]
        revision_uid = f"r{next_id:06d}"
        snapshot_rel = Path("revisions") / revision_uid / f"source{source_path.suffix.lower()}"
        snapshot = self.root / snapshot_rel
        staging = self.root / ".staging" / f"{revision_uid}-{uuid.uuid4().hex}.tmp"
        staging.parent.mkdir(exist_ok=True)
        with source_path.open("rb") as source_handle, staging.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if sha256_file(staging) != source_hash:
            staging.unlink(missing_ok=True)
            raise ValueError("Snapshot copy failed SHA-256 verification")
        snapshot.parent.mkdir(parents=True, exist_ok=False)
        os.replace(staging, snapshot)
        try:
            snapshot.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
        now = utc_now()
        try:
            with self.connection:
                current_latest = self.connection.execute(
                    "SELECT revision_uid FROM manuscript_revisions ORDER BY id DESC LIMIT 1"
                ).fetchone()
                current_parent_uid = current_latest[0] if current_latest else None
                if current_parent_uid != parent_uid:
                    raise ValueError(
                        f"Latest revision changed during import: expected {parent_uid}, "
                        f"found {current_parent_uid}"
                    )
                self.connection.execute(
                    """INSERT INTO manuscript_revisions(
                        revision_uid,parent_revision_uid,source_name,source_format,source_sha256,
                        source_size,snapshot_path,created_at,title,author,language,content_map_hash,
                        paragraph_count,chapter_count,warnings_json,metadata_json,status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        revision_uid, parent_uid, _source_name or source_path.name, document.format, source_hash,
                        source_size, snapshot_rel.as_posix(), now, resolved_title, resolved_author,
                        resolved_language,
                        document.content_map_hash, len(document.paragraphs), len(document.chapters),
                        json.dumps(document.warnings, ensure_ascii=False),
                        json.dumps(
                            {**document.metadata, **(_revision_metadata or {})},
                            ensure_ascii=False, sort_keys=True,
                        ),
                        "ready",
                    ),
                )
                for chapter, (chapter_id, status) in zip(document.chapters, chapter_reconciliation):
                    self.connection.execute(
                        """INSERT INTO chapters(
                            revision_uid,stable_id,source_order,title,start_paragraph,end_paragraph,reconciliation_status
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            revision_uid, chapter_id, chapter.source_order, chapter.title,
                            chapter.start_paragraph, chapter.end_paragraph, status,
                        ),
                    )
                current_ids: dict[int, int] = {}
                for record in paragraph_records:
                    cursor = self.connection.execute(
                        """INSERT INTO paragraphs(
                            revision_uid,chapter_stable_id,stable_id,source_order,chapter_order,text,
                            normalized_text,text_fingerprint,occurrence_index,context_before,context_after,
                            style_json,runs_json,source_ref_json,reconciliation_status
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            revision_uid,
                            record["chapter_stable_id"],
                            record["stable_id"],
                            record["source_order"],
                            record["chapter_order"],
                            record["text"],
                            record["normalized_text"],
                            record["text_fingerprint"],
                            record["occurrence_index"],
                            record["context_before"],
                            record["context_after"],
                            json.dumps(record["style_json"], ensure_ascii=False, sort_keys=True),
                            json.dumps(record["runs_json"], ensure_ascii=False, sort_keys=True),
                            json.dumps(record["source_ref_json"], ensure_ascii=False, sort_keys=True),
                            record["reconciliation_status"],
                        ),
                    )
                    current_ids[record["source_order"]] = cursor.lastrowid
                for change in changes:
                    self.connection.execute(
                        """INSERT INTO paragraph_changes(
                            revision_uid,paragraph_stable_id,previous_paragraph_id,current_paragraph_id,
                            change_type,similarity,details_json
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            revision_uid, change["paragraph_stable_id"], change["previous_paragraph_id"],
                            current_ids.get(change["new_index"]), change["change_type"],
                            change["similarity"],
                            json.dumps(change.get("details", {}), ensure_ascii=False, sort_keys=True),
                        ),
                    )
                self._store_revision_diff(
                    revision_uid, parent_uid, diff_groups, diff_candidates, current_ids, now
                )
                self.connection.execute(
                    "UPDATE projects SET updated_at=?,title=?,author=?,language=?",
                    (now, resolved_title, resolved_author, resolved_language),
                )
                if _transaction_hook is not None:
                    _transaction_hook(revision_uid, now)
                self.connection.execute(
                    "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
                    (
                        "revision.imported", revision_uid,
                        json.dumps({"sha256": source_hash, "paragraphs": len(document.paragraphs)}), now,
                    ),
                )
        except Exception:
            try:
                snapshot.chmod(stat.S_IWRITE | stat.S_IREAD)
                snapshot.unlink()
                snapshot.parent.rmdir()
            except OSError:
                pass
            raise
        return self.revision(revision_uid)

    def record_decision(
        self,
        decision_id: str,
        decision_type: str,
        *,
        payload: dict | None = None,
        revision_uid: str | None = None,
        source_fragment_id: str | None = None,
        actor: str = "author",
    ) -> None:
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO author_decisions(
                    decision_id,decision_type,source_revision_uid,source_fragment_id,payload_json,
                    selected_json,status,created_at,updated_at,legacy_decision_id
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    decision_id, decision_type, revision_uid, source_fragment_id,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), None,
                    "pending", now, now,
                ),
            )
            self.connection.execute(
                """INSERT INTO decision_events(
                    decision_id,status,selected_json,actor,rationale,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (decision_id, "pending", None, actor, None, now),
            )

    def decide(
        self,
        decision_id: str,
        status_value: str,
        *,
        selected: object | None = None,
        actor: str = "author",
        rationale: str | None = None,
    ) -> None:
        if status_value not in {"pending", "accepted", "approved", "rejected", "deferred"}:
            raise ValueError(f"Unsupported decision status: {status_value}")
        if not self.connection.execute(
            "SELECT 1 FROM author_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone():
            raise ValueError(f"Unknown decision: {decision_id}")
        now = utc_now()
        selected_json = json.dumps(selected, ensure_ascii=False) if selected is not None else None
        with self.connection:
            self.connection.execute(
                "UPDATE author_decisions SET status=?,selected_json=?,updated_at=? WHERE decision_id=?",
                (status_value, selected_json, now, decision_id),
            )
            self.connection.execute(
                """INSERT INTO decision_events(
                    decision_id,status,selected_json,actor,rationale,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (decision_id, status_value, selected_json, actor, rationale, now),
            )

    def record_finding(
        self,
        finding_id: str,
        revision_uid: str,
        *,
        quote: str,
        recommendation: str,
        paragraph_stable_id: str | None = None,
        anchor: dict | None = None,
        status_value: str = "pending",
    ) -> None:
        if not self.connection.execute(
            "SELECT 1 FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
        ).fetchone():
            raise ValueError(f"Unknown revision: {revision_uid}")
        with self.connection:
            self.connection.execute(
                """INSERT INTO editorial_findings(
                    finding_id,source_revision_uid,paragraph_stable_id,anchor_json,exact_quote,
                    recommendation,status,created_at,legacy_finding_id
                ) VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (
                    finding_id, revision_uid, paragraph_stable_id,
                    json.dumps(anchor or {}, ensure_ascii=False, sort_keys=True), quote,
                    recommendation, status_value, utc_now(),
                ),
            )

    def add_correction(
        self,
        correction_id: str,
        revision_uid: str,
        *,
        paragraph_stable_id: str,
        exact_before: str,
        replacement: str,
        change_class: str,
        start_offset: int | None = None,
        end_offset: int | None = None,
        finding_id: str | None = None,
        group_uid: str | None = None,
        category: str | None = None,
        rationale: str | None = None,
        actor: str = "editor",
    ) -> dict:
        if not correction_id.strip():
            raise ValueError("Correction ID is required")
        if change_class not in {"mechanical", "semantic"}:
            raise ValueError("Correction class must be mechanical or semantic")
        if not exact_before:
            raise ValueError("Correction before text cannot be empty")
        if exact_before == replacement:
            raise ValueError("Correction replacement must differ from before text")
        if not actor.strip():
            raise ValueError("Correction actor is required")
        paragraph = self.connection.execute(
            """SELECT * FROM paragraphs
               WHERE revision_uid=? AND stable_id=?""",
            (revision_uid, paragraph_stable_id),
        ).fetchone()
        if not paragraph:
            raise ValueError(
                f"Paragraph {paragraph_stable_id} does not belong to revision {revision_uid}"
            )
        if (start_offset is None) != (end_offset is None):
            raise ValueError("Both start and end offsets are required together")
        if start_offset is None:
            starts: list[int] = []
            cursor = 0
            while True:
                cursor = paragraph["text"].find(exact_before, cursor)
                if cursor < 0:
                    break
                starts.append(cursor)
                cursor += 1
            if len(starts) != 1:
                raise ValueError(
                    f"Exact before text occurs {len(starts)} times in paragraph; provide an unambiguous span"
                )
            start_offset = starts[0]
            end_offset = start_offset + len(exact_before)
        if start_offset < 0 or end_offset < start_offset:
            raise ValueError("Correction offsets are invalid")
        if paragraph["text"][start_offset:end_offset] != exact_before:
            raise ValueError("Correction offsets do not match the exact before text")
        if finding_id:
            finding = self.connection.execute(
                "SELECT source_revision_uid FROM editorial_findings WHERE finding_id=?",
                (finding_id,),
            ).fetchone()
            if not finding or finding[0] != revision_uid:
                raise ValueError("Correction finding must belong to the same source revision")
        now = utc_now()
        event_uid = stable_id("ce")
        with self.connection:
            self.connection.execute(
                """INSERT INTO corrections(
                    correction_id,source_revision_uid,paragraph_stable_id,finding_id,group_uid,
                    exact_before,replacement,start_offset,end_offset,change_class,category,
                    rationale,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    correction_id, revision_uid, paragraph_stable_id, finding_id, group_uid,
                    exact_before, replacement, start_offset, end_offset, change_class, category,
                    rationale, "pending", now, now,
                ),
            )
            self.connection.execute(
                """INSERT INTO correction_events(
                    event_uid,correction_id,status,actor,rationale,batch_uid,
                    source_revision_uid,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (event_uid, correction_id, "pending", actor, rationale, None, revision_uid, now),
            )
        return self.correction(correction_id)

    def correction(self, correction_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM corrections WHERE correction_id=?", (correction_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown correction: {correction_id}")
        payload = dict(row)
        payload["history"] = [
            dict(event) for event in self.connection.execute(
                """SELECT * FROM correction_events WHERE correction_id=?
                   ORDER BY sequence""",
                (correction_id,),
            )
        ]
        return payload

    def corrections(self, revision_uid: str | None = None) -> list[dict]:
        if revision_uid:
            rows = self.connection.execute(
                """SELECT correction_id FROM corrections
                   WHERE source_revision_uid=? ORDER BY correction_id""",
                (revision_uid,),
            )
        else:
            rows = self.connection.execute(
                "SELECT correction_id FROM corrections ORDER BY created_at,correction_id"
            )
        return [self.correction(row[0]) for row in rows]

    def decide_corrections(
        self,
        decision: str,
        *,
        correction_ids: list[str] | None = None,
        group_uid: str | None = None,
        all_mechanical: bool = False,
        revision_uid: str | None = None,
        actor: str = "author",
        rationale: str | None = None,
        author_confirmed: bool = False,
        confirm_semantic_group: bool = False,
    ) -> list[dict]:
        if decision not in {"pending", "approved", "rejected", "deferred"}:
            raise ValueError(f"Unsupported correction decision: {decision}")
        if not actor.strip():
            raise ValueError("Correction decision actor is required")
        selectors = int(bool(correction_ids)) + int(bool(group_uid)) + int(all_mechanical)
        if selectors != 1:
            raise ValueError("Select correction IDs, one group, or all pending mechanical corrections")
        if correction_ids:
            selected_ids = list(dict.fromkeys(correction_ids))
            placeholders = ",".join("?" for _ in selected_ids)
            rows = self.connection.execute(
                f"SELECT * FROM corrections WHERE correction_id IN ({placeholders})",
                selected_ids,
            ).fetchall()
            found = {row["correction_id"] for row in rows}
            missing = sorted(set(selected_ids) - found)
            if missing:
                raise ValueError(f"Unknown corrections: {missing}")
        elif group_uid:
            if revision_uid:
                rows = self.connection.execute(
                    """SELECT * FROM corrections
                       WHERE group_uid=? AND source_revision_uid=? ORDER BY correction_id""",
                    (group_uid, revision_uid),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM corrections WHERE group_uid=? ORDER BY correction_id",
                    (group_uid,),
                ).fetchall()
        else:
            if not revision_uid:
                raise ValueError("A source revision is required for all-mechanical approval")
            rows = self.connection.execute(
                """SELECT * FROM corrections
                   WHERE source_revision_uid=? AND change_class='mechanical' AND status='pending'
                   ORDER BY correction_id""",
                (revision_uid,),
            ).fetchall()
        if not rows:
            raise ValueError("No corrections matched the decision scope")
        source_revisions = {row["source_revision_uid"] for row in rows}
        if len(source_revisions) != 1:
            raise ValueError("A correction decision batch cannot span source revisions")
        if any(row["status"] == "applied" for row in rows):
            raise ValueError("Applied correction decisions are immutable")
        if decision == "approved" and all_mechanical:
            unresolved_by_revision: dict[str, set[str]] = {}
            for row in rows:
                if row["source_revision_uid"] not in unresolved_by_revision:
                    unresolved_by_revision[row["source_revision_uid"]] = {
                        member["stable_id"]
                        for group in self.reconciliation_queue(row["source_revision_uid"])
                        if group["entity_type"] == "paragraph"
                        for member in group["members"] if member["side"] == "after"
                    }
                if row["paragraph_stable_id"] in unresolved_by_revision[row["source_revision_uid"]]:
                    raise ValueError(
                        "Batch mechanical approval includes an ambiguous target: "
                        f"{row['correction_id']}"
                    )
        semantic = [row["correction_id"] for row in rows if row["change_class"] == "semantic"]
        if decision == "approved" and not author_confirmed:
            raise ValueError("Explicit author approval confirmation is required")
        if decision == "approved" and semantic and (group_uid or len(semantic) > 1):
            if not confirm_semantic_group:
                raise ValueError(
                    "Semantic group approval requires explicit semantic-group confirmation"
                )
        now = utc_now()
        batch_uid = stable_id("batch")
        with self.connection:
            for row in sorted(rows, key=lambda item: item["correction_id"]):
                self.connection.execute(
                    "UPDATE corrections SET status=?,updated_at=? WHERE correction_id=?",
                    (decision, now, row["correction_id"]),
                )
                self.connection.execute(
                    """INSERT INTO correction_events(
                        event_uid,correction_id,status,actor,rationale,batch_uid,
                        source_revision_uid,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        stable_id("ce"), row["correction_id"], decision, actor, rationale,
                        batch_uid, row["source_revision_uid"], now,
                    ),
                )
        return [self.correction(row["correction_id"]) for row in rows]

    def _correction_rows(
        self, revision_uid: str, correction_ids: list[str] | None
    ) -> list[sqlite3.Row]:
        if correction_ids is None:
            return self.connection.execute(
                """SELECT * FROM corrections
                   WHERE source_revision_uid=? AND status='approved'
                   ORDER BY correction_id""",
                (revision_uid,),
            ).fetchall()
        selected_ids = list(dict.fromkeys(correction_ids))
        if not selected_ids:
            return []
        placeholders = ",".join("?" for _ in selected_ids)
        rows = self.connection.execute(
            f"SELECT * FROM corrections WHERE correction_id IN ({placeholders})",
            selected_ids,
        ).fetchall()
        found = {row["correction_id"] for row in rows}
        missing = sorted(set(selected_ids) - found)
        if missing:
            raise ValueError(f"Unknown corrections: {missing}")
        return sorted(rows, key=lambda row: row["correction_id"])

    def preview_correction_apply(
        self, revision_uid: str, *, correction_ids: list[str] | None = None
    ) -> dict:
        revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
        ).fetchone()
        if not revision:
            raise ValueError(f"Unknown revision: {revision_uid}")
        blockers: list[dict] = []

        def block(code: str, message: str, correction_id: str | None = None) -> None:
            item = {"code": code, "message": message}
            if correction_id:
                item["correction_id"] = correction_id
            blockers.append(item)

        latest = self.connection.execute(
            "SELECT revision_uid FROM manuscript_revisions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        if latest != revision_uid:
            block(
                "source_not_latest",
                f"Source revision {revision_uid} is stale; latest revision is {latest}",
            )
        snapshot = self.root / revision["snapshot_path"]
        if not snapshot.is_file():
            block("source_missing", f"Source snapshot is missing: {revision['snapshot_path']}")
        else:
            actual_hash = sha256_file(snapshot)
            if actual_hash != revision["source_sha256"]:
                block("source_hash_mismatch", "Source snapshot SHA-256 no longer matches the revision")
            try:
                document, _, source_size = extract_manuscript(snapshot)
                if source_size != revision["source_size"]:
                    block("source_size_mismatch", "Source snapshot size no longer matches the revision")
                if document.content_map_hash != revision["content_map_hash"]:
                    block("source_map_mismatch", "Source structured content map no longer matches")
            except (ValueError, OSError) as error:
                block("source_invalid", str(error))
        rows = self._correction_rows(revision_uid, correction_ids)
        if not rows:
            block("no_approved_corrections", "No approved corrections were selected")
        unresolved_ids = {
            member["stable_id"]
            for group in self.reconciliation_queue(revision_uid)
            if group["entity_type"] == "paragraph"
            for member in group["members"] if member["side"] == "after"
        }
        patches: list[dict] = []
        for row in rows:
            correction_id = row["correction_id"]
            if row["source_revision_uid"] != revision_uid:
                block(
                    "wrong_source_revision",
                    f"Correction belongs to {row['source_revision_uid']}, not {revision_uid}",
                    correction_id,
                )
                continue
            if row["status"] != "approved":
                block(
                    "not_approved", f"Correction status is {row['status']}", correction_id
                )
            paragraph = self.connection.execute(
                """SELECT * FROM paragraphs
                   WHERE revision_uid=? AND stable_id=?""",
                (revision_uid, row["paragraph_stable_id"]),
            ).fetchone()
            if not paragraph:
                block("paragraph_missing", "Target paragraph is missing", correction_id)
                continue
            if row["paragraph_stable_id"] in unresolved_ids:
                block(
                    "ambiguous_reconciliation",
                    "Target paragraph still requires reconciliation",
                    correction_id,
                )
            start = row["start_offset"]
            end = row["end_offset"]
            if start is None or end is None or paragraph["text"][start:end] != row["exact_before"]:
                block(
                    "exact_span_mismatch",
                    "Exact before text no longer matches the stored paragraph span",
                    correction_id,
                )
                continue
            patches.append(
                {
                    "correction_id": correction_id,
                    "paragraph_stable_id": row["paragraph_stable_id"],
                    "source_order": paragraph["source_order"],
                    "start": start,
                    "end": end,
                    "before": row["exact_before"],
                    "after": row["replacement"],
                    "change_class": row["change_class"],
                    "status": row["status"],
                    "updated_at": row["updated_at"],
                }
            )
        by_paragraph: dict[str, list[dict]] = {}
        for patch in patches:
            by_paragraph.setdefault(patch["paragraph_stable_id"], []).append(patch)
        for paragraph_id, values in by_paragraph.items():
            ordered = sorted(values, key=lambda item: (item["start"], item["end"], item["correction_id"]))
            for left, right in zip(ordered, ordered[1:]):
                if right["start"] < left["end"]:
                    message = (
                        f"Corrections {left['correction_id']} and {right['correction_id']} "
                        f"overlap in paragraph {paragraph_id}"
                    )
                    block("overlapping_corrections", message, left["correction_id"])
                    block("overlapping_corrections", message, right["correction_id"])
        if snapshot.is_file() and revision["source_format"] in {"docx", "text", "markdown"}:
            for message in renderability_errors(snapshot, revision["source_format"], patches):
                block("formatting_collision", message)
        elif revision["source_format"] not in {"docx", "text", "markdown"}:
            block(
                "unsupported_format",
                f"Safe apply is not supported for {revision['source_format']}",
            )
        canonical = {
            "source_revision_uid": revision_uid,
            "source_sha256": revision["source_sha256"],
            "source_content_map_hash": revision["content_map_hash"],
            "patches": sorted(
                patches, key=lambda item: (item["source_order"], item["start"], item["correction_id"])
            ),
        }
        plan_hash = sha256_bytes(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return {
            "mode": "dry-run",
            "ready": not blockers,
            "plan_hash": plan_hash,
            "source_revision_uid": revision_uid,
            "source_sha256": revision["source_sha256"],
            "correction_ids": [patch["correction_id"] for patch in canonical["patches"]],
            "patches": canonical["patches"],
            "blockers": sorted(
                blockers,
                key=lambda item: (item["code"], item.get("correction_id", ""), item["message"]),
            ),
            "notice": "Preview only; no file or revision was changed.",
        }

    def apply_corrections(
        self,
        revision_uid: str,
        *,
        plan_hash: str,
        correction_ids: list[str] | None = None,
        actor: str = "author",
        author_confirmed: bool = False,
    ) -> dict:
        if not author_confirmed:
            raise ValueError("Explicit author apply confirmation is required")
        if not actor.strip():
            raise ValueError("Apply actor is required")
        preview = self.preview_correction_apply(revision_uid, correction_ids=correction_ids)
        if not preview["ready"]:
            raise ValueError(
                "Apply is blocked: " + "; ".join(item["message"] for item in preview["blockers"])
            )
        if plan_hash != preview["plan_hash"]:
            raise ValueError("Apply plan hash changed; run a new preview and approve that exact plan")
        revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
        ).fetchone()
        snapshot = self.root / revision["snapshot_path"]
        suffix = Path(revision["source_name"]).suffix or f".{revision['source_format']}"
        stage = self.root / ".staging" / f"apply-{uuid.uuid4().hex}{suffix}"
        application_uid = stable_id("apply")
        correction_id_values = preview["correction_ids"]
        try:
            render_staged_revision(snapshot, stage, revision["source_format"], preview["patches"])
            rendered, _, _ = extract_manuscript(stage)
            source_rows = self.connection.execute(
                "SELECT source_order,text FROM paragraphs WHERE revision_uid=? ORDER BY source_order",
                (revision_uid,),
            ).fetchall()
            expected = {row["source_order"]: row["text"] for row in source_rows}
            for patch in sorted(preview["patches"], key=lambda item: item["start"], reverse=True):
                text = expected[patch["source_order"]]
                if text[patch["start"]:patch["end"]] != patch["before"]:
                    raise ValueError(
                        f"Exact source span changed for correction {patch['correction_id']}"
                    )
                expected[patch["source_order"]] = (
                    text[:patch["start"]] + patch["after"] + text[patch["end"]:]
                )
            actual = {paragraph.source_order: paragraph.text for paragraph in rendered.paragraphs}
            if actual != expected:
                raise ValueError("Rendered manuscript verification failed; no revision was committed")

            def commit_application(result_revision_uid: str, created_at: str) -> None:
                self.connection.execute(
                    """INSERT INTO change_applications(
                        application_uid,operation,source_revision_uid,result_revision_uid,
                        rollback_of_uid,plan_hash,source_sha256,correction_ids_json,actor,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        application_uid, "apply", revision_uid, result_revision_uid, None,
                        plan_hash, revision["source_sha256"],
                        json.dumps(correction_id_values, ensure_ascii=False), actor, created_at,
                    ),
                )
                for correction_id in correction_id_values:
                    updated = self.connection.execute(
                        """UPDATE corrections SET status='applied',updated_at=?
                           WHERE correction_id=? AND status='approved'""",
                        (created_at, correction_id),
                    )
                    if updated.rowcount != 1:
                        raise ValueError(
                            f"Correction approval changed during apply: {correction_id}"
                        )
                    self.connection.execute(
                        """INSERT INTO correction_events(
                            event_uid,correction_id,status,actor,rationale,batch_uid,
                            source_revision_uid,created_at
                        ) VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            stable_id("ce"), correction_id, "applied", actor,
                            f"Applied by {application_uid}", application_uid, revision_uid, created_at,
                        ),
                    )
                self.connection.execute(
                    "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
                    (
                        "corrections.applied", result_revision_uid,
                        json.dumps({
                            "application_uid": application_uid,
                            "source_revision_uid": revision_uid,
                            "plan_hash": plan_hash,
                            "correction_ids": correction_id_values,
                            "actor": actor,
                        }, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )

            source_name = Path(revision["source_name"])
            output_name = f"{source_name.stem}.editroast{suffix}"
            result = self.import_manuscript(
                stage,
                _source_name=output_name,
                _revision_metadata={
                    "generated_by": "safe_apply",
                    "application_uid": application_uid,
                    "plan_hash": plan_hash,
                    "source_revision_uid": revision_uid,
                },
                _transaction_hook=commit_application,
                _expected_parent_uid=revision_uid,
            )
        finally:
            stage.unlink(missing_ok=True)
        return {
            "application_uid": application_uid,
            "operation": "apply",
            "source_revision_uid": revision_uid,
            "result_revision_uid": result["revision_uid"],
            "result_snapshot": result["snapshot"],
            "plan_hash": plan_hash,
            "correction_ids": correction_id_values,
        }

    def preview_rollback(self, application_uid: str) -> dict:
        application = self.connection.execute(
            "SELECT * FROM change_applications WHERE application_uid=?", (application_uid,)
        ).fetchone()
        if not application or application["operation"] != "apply":
            raise ValueError(f"Unknown apply operation: {application_uid}")
        blockers: list[dict] = []
        latest = self.connection.execute(
            "SELECT revision_uid FROM manuscript_revisions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        if latest != application["result_revision_uid"]:
            blockers.append({
                "code": "result_not_latest",
                "message": (
                    f"Applied revision {application['result_revision_uid']} is not latest; "
                    f"current latest is {latest}"
                ),
            })
        source_revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?",
            (application["source_revision_uid"],),
        ).fetchone()
        result_revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?",
            (application["result_revision_uid"],),
        ).fetchone()
        for label, revision in (("rollback_source", source_revision), ("applied_result", result_revision)):
            path = self.root / revision["snapshot_path"]
            if not path.is_file() or sha256_file(path) != revision["source_sha256"]:
                blockers.append({
                    "code": f"{label}_integrity",
                    "message": f"{label.replace('_', ' ')} snapshot failed SHA-256 verification",
                })
        canonical = {
            "operation": "rollback",
            "application_uid": application_uid,
            "source_revision_uid": application["source_revision_uid"],
            "applied_revision_uid": application["result_revision_uid"],
            "restore_sha256": source_revision["source_sha256"],
            "applied_sha256": result_revision["source_sha256"],
        }
        plan_hash = sha256_bytes(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return {
            **canonical,
            "mode": "dry-run",
            "ready": not blockers,
            "plan_hash": plan_hash,
            "blockers": blockers,
            "notice": "Rollback preview only; rollback creates another immutable revision.",
        }

    def rollback_application(
        self,
        application_uid: str,
        *,
        plan_hash: str,
        actor: str = "author",
        author_confirmed: bool = False,
    ) -> dict:
        if not author_confirmed:
            raise ValueError("Explicit author rollback confirmation is required")
        if not actor.strip():
            raise ValueError("Rollback actor is required")
        preview = self.preview_rollback(application_uid)
        if not preview["ready"]:
            raise ValueError(
                "Rollback is blocked: "
                + "; ".join(item["message"] for item in preview["blockers"])
            )
        if plan_hash != preview["plan_hash"]:
            raise ValueError("Rollback plan hash changed; run a new preview")
        application = self.connection.execute(
            "SELECT * FROM change_applications WHERE application_uid=?", (application_uid,)
        ).fetchone()
        applied_result = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?",
            (application["result_revision_uid"],),
        ).fetchone()
        source_revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?",
            (application["source_revision_uid"],),
        ).fetchone()
        source = self.root / source_revision["snapshot_path"]
        suffix = Path(source_revision["source_name"]).suffix or f".{source_revision['source_format']}"
        stage = self.root / ".staging" / f"rollback-{uuid.uuid4().hex}{suffix}"
        rollback_uid = stable_id("rollback")
        try:
            with source.open("rb") as source_handle, stage.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
                target_handle.flush()
                os.fsync(target_handle.fileno())

            def commit_rollback(result_revision_uid: str, created_at: str) -> None:
                self.connection.execute(
                    """INSERT INTO change_applications(
                        application_uid,operation,source_revision_uid,result_revision_uid,
                        rollback_of_uid,plan_hash,source_sha256,correction_ids_json,actor,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rollback_uid, "rollback", application["result_revision_uid"],
                        result_revision_uid, application_uid, plan_hash,
                        applied_result["source_sha256"], application["correction_ids_json"],
                        actor, created_at,
                    ),
                )
                self.connection.execute(
                    "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
                    (
                        "corrections.rolled_back", result_revision_uid,
                        json.dumps({
                            "rollback_uid": rollback_uid,
                            "rollback_of_uid": application_uid,
                            "restored_revision_uid": application["source_revision_uid"],
                            "actor": actor,
                        }, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )

            source_name = Path(source_revision["source_name"])
            result = self.import_manuscript(
                stage,
                _source_name=f"{source_name.stem}.rollback{suffix}",
                _revision_metadata={
                    "generated_by": "safe_rollback",
                    "rollback_uid": rollback_uid,
                    "rollback_of_uid": application_uid,
                    "restored_revision_uid": application["source_revision_uid"],
                    "plan_hash": plan_hash,
                },
                _transaction_hook=commit_rollback,
                _expected_parent_uid=application["result_revision_uid"],
            )
        finally:
            stage.unlink(missing_ok=True)
        return {
            "application_uid": rollback_uid,
            "operation": "rollback",
            "rollback_of_uid": application_uid,
            "source_revision_uid": application["result_revision_uid"],
            "result_revision_uid": result["revision_uid"],
            "result_snapshot": result["snapshot"],
            "plan_hash": plan_hash,
        }

    def application_history(self) -> list[dict]:
        history = []
        for row in self.connection.execute(
            "SELECT * FROM change_applications ORDER BY created_at,application_uid"
        ):
            item = dict(row)
            item["correction_ids"] = json.loads(item.pop("correction_ids_json"))
            history.append(item)
        return history

    def _run_row(self, run_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown run: {run_id}")
        return row

    def _record_run_event(
        self,
        run_id: str,
        event_type: str,
        *,
        actor: str,
        unit_id: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        attempt_uid: str | None = None,
        payload: dict | None = None,
        created_at: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO run_events(
                event_uid,run_id,unit_id,event_type,from_status,to_status,attempt_uid,
                actor,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                stable_id("re"), run_id, unit_id, event_type, from_status, to_status,
                attempt_uid, actor, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                created_at or utc_now(),
            ),
        )

    def create_run(
        self,
        run_id: str,
        revision_uid: str,
        *,
        run_type: str,
        scope: str = "chapter",
        actor: str = "agent",
        provenance: dict | None = None,
    ) -> dict:
        if not run_id.strip():
            raise ValueError("Run ID is required")
        if not run_type.strip():
            raise ValueError("Run type is required")
        if scope not in {"chapter", "paragraph"}:
            raise ValueError("Run scope must be chapter or paragraph")
        if not actor.strip():
            raise ValueError("Run actor is required")
        revision = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
        ).fetchone()
        if not revision:
            raise ValueError(f"Unknown revision: {revision_uid}")
        table = "chapters" if scope == "chapter" else "paragraphs"
        source_rows = self.connection.execute(
            f"SELECT stable_id,source_order FROM {table} WHERE revision_uid=? ORDER BY source_order",
            (revision_uid,),
        ).fetchall()
        if not source_rows:
            raise ValueError(f"Revision {revision_uid} has no {scope} units")
        units = [
            {
                "unit_id": row["stable_id"],
                "source_stable_id": row["stable_id"],
                "source_order": row["source_order"],
            }
            for row in source_rows
        ]
        canonical_plan = {
            "revision_uid": revision_uid,
            "content_map_hash": revision["content_map_hash"],
            "run_type": run_type,
            "scope": scope,
            "units": units,
        }
        plan_hash = sha256_bytes(
            json.dumps(
                canonical_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        existing = self.connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing:
            existing_provenance = json.loads(existing["provenance_json"])
            if existing_provenance.get("plan_hash") != plan_hash:
                raise ValueError(f"Run {run_id} already exists with a different plan")
            payload = self.run_status(run_id)
            payload["idempotent"] = True
            return payload
        now = utc_now()
        stored_provenance = {
            **(provenance or {}),
            "scope": scope,
            "plan_hash": plan_hash,
            "source_content_map_hash": revision["content_map_hash"],
        }
        with self.connection:
            self.connection.execute(
                """INSERT INTO runs(
                    run_id,revision_uid,run_type,status,provenance_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id, revision_uid, run_type, "pending",
                    json.dumps(stored_provenance, ensure_ascii=False, sort_keys=True), now, now,
                ),
            )
            for unit in units:
                self.connection.execute(
                    """INSERT INTO run_units(
                        run_id,unit_id,source_kind,source_stable_id,source_order,status,updated_at
                    ) VALUES(?,?,?,?,?,'pending',?)""",
                    (
                        run_id, unit["unit_id"], scope, unit["source_stable_id"],
                        unit["source_order"], now,
                    ),
                )
            self._record_run_event(
                run_id, "run.created", actor=actor, to_status="pending",
                payload={"plan_hash": plan_hash, "scope": scope, "unit_count": len(units)},
                created_at=now,
            )
        return self.run_status(run_id)

    def _verified_run_units(self, run_id: str) -> tuple[set[str], list[dict]]:
        verified: set[str] = set()
        issues: list[dict] = []
        for row in self.connection.execute(
            "SELECT * FROM run_units WHERE run_id=? ORDER BY source_order,unit_id", (run_id,)
        ):
            if row["status"] != "completed":
                continue
            if not row["result_path"] or not row["result_sha256"]:
                issues.append({
                    "unit_id": row["unit_id"],
                    "code": "result_metadata_missing",
                    "message": "Completed unit has no result path or SHA-256",
                })
                continue
            relative = _safe_project_relative(row["result_path"])
            if relative is None:
                issues.append({
                    "unit_id": row["unit_id"],
                    "code": "unsafe_result_path",
                    "message": "Result path is not project-relative",
                })
                continue
            path = self.root / relative
            if not path.is_file():
                issues.append({
                    "unit_id": row["unit_id"],
                    "code": "result_missing",
                    "message": f"Result file is missing: {row['result_path']}",
                })
                continue
            if sha256_file(path) != row["result_sha256"]:
                issues.append({
                    "unit_id": row["unit_id"],
                    "code": "result_hash_mismatch",
                    "message": f"Result file changed: {row['result_path']}",
                })
                continue
            verified.add(row["unit_id"])
        return verified, issues

    def run_status(self, run_id: str) -> dict:
        run = self._run_row(run_id)
        units = [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM run_units WHERE run_id=? ORDER BY source_order,unit_id", (run_id,)
            )
        ]
        if not units:
            raise ValueError(f"Run {run_id} has no resumable unit plan")
        verified, issues = self._verified_run_units(run_id)
        plan = json.loads(run["provenance_json"])
        revision = self.connection.execute(
            "SELECT content_map_hash FROM manuscript_revisions WHERE revision_uid=?",
            (run["revision_uid"],),
        ).fetchone()
        if not revision or revision[0] != plan.get("source_content_map_hash"):
            issues.append({
                "unit_id": None,
                "code": "source_plan_mismatch",
                "message": "Run source revision no longer matches its immutable plan",
            })
        counts = {status: 0 for status in ("pending", "in_progress", "completed", "failed")}
        for unit in units:
            counts[unit["status"]] += 1
            unit["verified"] = unit["unit_id"] in verified
        ready = len(verified) == len(units) and not issues
        effective_status = "completed" if ready else run["status"]
        if run["status"] == "completed" and not ready:
            effective_status = "integrity_error"
        return {
            "run_id": run_id,
            "revision_uid": run["revision_uid"],
            "run_type": run["run_type"],
            "status": run["status"],
            "effective_status": effective_status,
            "plan": plan,
            "unit_count": len(units),
            "counts": counts,
            "verified_completed": len(verified),
            "progress_percent": round(len(verified) * 100 / len(units), 2),
            "coverage_complete": ready,
            "issues": issues,
            "units": units,
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
        }

    def claim_run_unit(self, run_id: str, *, worker_id: str, actor: str = "agent") -> dict:
        if not worker_id.strip():
            raise ValueError("Worker ID is required")
        run = self._run_row(run_id)
        if run["status"] in {"paused", "blocked_by_usage_limit", "needs_author_decision"}:
            raise ValueError(f"Run {run_id} is {run['status']}; resume it before claiming work")
        if run["status"] == "completed":
            return {"run_id": run_id, "next_unit": None, "coverage": self.run_status(run_id)}
        unit = self.connection.execute(
            """SELECT * FROM run_units
               WHERE run_id=? AND status='pending' ORDER BY source_order,unit_id LIMIT 1""",
            (run_id,),
        ).fetchone()
        if not unit:
            return {"run_id": run_id, "next_unit": None, "coverage": self.run_status(run_id)}
        now = utc_now()
        attempt_uid = stable_id("attempt")
        with self.connection:
            updated = self.connection.execute(
                """UPDATE run_units SET status='in_progress',attempt_count=attempt_count+1,
                   active_attempt_uid=?,worker_id=?,error=NULL,started_at=?,completed_at=NULL,updated_at=?
                   WHERE id=? AND status='pending'""",
                (attempt_uid, worker_id, now, now, unit["id"]),
            )
            if updated.rowcount != 1:
                raise ValueError("Run unit was claimed concurrently; request the next unit again")
            self.connection.execute(
                "UPDATE runs SET status='running',updated_at=? WHERE run_id=?",
                (now, run_id),
            )
            self._record_run_event(
                run_id, "unit.started", actor=actor, unit_id=unit["unit_id"],
                from_status="pending", to_status="in_progress", attempt_uid=attempt_uid,
                payload={"worker_id": worker_id}, created_at=now,
            )
        claimed = dict(self.connection.execute(
            "SELECT * FROM run_units WHERE id=?", (unit["id"],)
        ).fetchone())
        return {"run_id": run_id, "next_unit": claimed}

    def complete_run_unit(
        self,
        run_id: str,
        unit_id: str,
        *,
        attempt_uid: str,
        result: str | Path,
        actor: str = "agent",
    ) -> dict:
        unit = self.connection.execute(
            "SELECT * FROM run_units WHERE run_id=? AND unit_id=?", (run_id, unit_id)
        ).fetchone()
        if not unit:
            raise ValueError(f"Unknown unit {unit_id} for run {run_id}")
        result_path = Path(result).resolve()
        try:
            relative = result_path.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Run result must be inside the project") from error
        if not result_path.is_file() or result_path.stat().st_size == 0:
            raise ValueError(f"Run result is missing or empty: {result_path}")
        relative_value = relative.as_posix()
        result_hash = sha256_file(result_path)
        if unit["status"] == "completed":
            if (
                unit["active_attempt_uid"] == attempt_uid
                and unit["result_path"] == relative_value
                and unit["result_sha256"] == result_hash
            ):
                payload = self.run_status(run_id)
                payload["idempotent"] = True
                return payload
            raise ValueError(f"Run unit {unit_id} is already completed with a different result")
        if unit["status"] != "in_progress" or unit["active_attempt_uid"] != attempt_uid:
            raise ValueError("Stale or invalid run attempt; claim the unit again")
        now = utc_now()
        with self.connection:
            updated = self.connection.execute(
                """UPDATE run_units SET status='completed',result_path=?,result_sha256=?,
                   error=NULL,completed_at=?,updated_at=?
                   WHERE id=? AND status='in_progress' AND active_attempt_uid=?""",
                (relative_value, result_hash, now, now, unit["id"], attempt_uid),
            )
            if updated.rowcount != 1:
                raise ValueError("Run attempt changed before the checkpoint was committed")
            self._record_run_event(
                run_id, "unit.completed", actor=actor, unit_id=unit_id,
                from_status="in_progress", to_status="completed", attempt_uid=attempt_uid,
                payload={"result_path": relative_value, "result_sha256": result_hash},
                created_at=now,
            )
            remaining = self.connection.execute(
                "SELECT COUNT(*) FROM run_units WHERE run_id=? AND status!='completed'", (run_id,)
            ).fetchone()[0]
            self.connection.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE run_id=?",
                ("completed" if remaining == 0 else "running", now, run_id),
            )
            if remaining == 0:
                self._record_run_event(
                    run_id, "run.completed", actor=actor, from_status="running",
                    to_status="completed", payload={"unit_count": self.connection.execute(
                        "SELECT COUNT(*) FROM run_units WHERE run_id=?", (run_id,)
                    ).fetchone()[0]}, created_at=now,
                )
        return self.run_status(run_id)

    def fail_run_unit(
        self,
        run_id: str,
        unit_id: str,
        *,
        attempt_uid: str,
        error: str,
        actor: str = "agent",
    ) -> dict:
        if not error.strip():
            raise ValueError("A failure reason is required")
        unit = self.connection.execute(
            "SELECT * FROM run_units WHERE run_id=? AND unit_id=?", (run_id, unit_id)
        ).fetchone()
        if not unit:
            raise ValueError(f"Unknown unit {unit_id} for run {run_id}")
        if unit["status"] != "in_progress" or unit["active_attempt_uid"] != attempt_uid:
            raise ValueError("Stale or invalid run attempt")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """UPDATE run_units SET status='failed',error=?,updated_at=?
                   WHERE id=? AND status='in_progress' AND active_attempt_uid=?""",
                (error, now, unit["id"], attempt_uid),
            )
            self.connection.execute(
                "UPDATE runs SET status='paused',updated_at=? WHERE run_id=?", (now, run_id)
            )
            self._record_run_event(
                run_id, "unit.failed", actor=actor, unit_id=unit_id,
                from_status="in_progress", to_status="failed", attempt_uid=attempt_uid,
                payload={"error": error}, created_at=now,
            )
        return self.run_status(run_id)

    def checkpoint_run(
        self,
        run_id: str,
        status_value: str,
        *,
        actor: str = "agent",
        reason: str | None = None,
    ) -> dict:
        allowed = {"paused", "blocked_by_usage_limit", "needs_author_decision"}
        if status_value not in allowed:
            raise ValueError(f"Checkpoint status must be one of: {sorted(allowed)}")
        run = self._run_row(run_id)
        if run["status"] == "completed":
            raise ValueError("A completed run cannot be paused")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE run_id=?",
                (status_value, now, run_id),
            )
            self._record_run_event(
                run_id, "run.checkpointed", actor=actor, from_status=run["status"],
                to_status=status_value, payload={"reason": reason}, created_at=now,
            )
        return self.run_status(run_id)

    def resume_run(self, run_id: str, *, actor: str = "agent") -> dict:
        run = self._run_row(run_id)
        current = self.run_status(run_id)
        if run["status"] == "completed" and current["coverage_complete"]:
            return current
        if any(issue["code"] == "source_plan_mismatch" for issue in current["issues"]):
            raise ValueError("Run source revision no longer matches its immutable plan")
        now = utc_now()
        with self.connection:
            verified_ids = {
                unit["unit_id"] for unit in current["units"] if unit["verified"]
            }
            abandoned = [
                unit for unit in self.connection.execute(
                    """SELECT unit_id,status,active_attempt_uid FROM run_units
                       WHERE run_id=? AND status IN ('in_progress','failed','completed')
                       ORDER BY source_order,unit_id""",
                    (run_id,),
                ).fetchall()
                if unit["status"] != "completed" or unit["unit_id"] not in verified_ids
            ]
            for unit in abandoned:
                self.connection.execute(
                    """UPDATE run_units SET status='pending',active_attempt_uid=NULL,worker_id=NULL,
                       result_path=NULL,result_sha256=NULL,error=NULL,
                       started_at=NULL,completed_at=NULL,updated_at=?
                       WHERE run_id=? AND unit_id=?""",
                    (now, run_id, unit["unit_id"]),
                )
                self._record_run_event(
                    run_id, "unit.requeued", actor=actor, unit_id=unit["unit_id"],
                    from_status=unit["status"], to_status="pending",
                    attempt_uid=unit["active_attempt_uid"], created_at=now,
                )
            self.connection.execute(
                "UPDATE runs SET status='pending',updated_at=? WHERE run_id=?", (now, run_id)
            )
            self._record_run_event(
                run_id, "run.resumed", actor=actor, from_status=run["status"],
                to_status="pending", payload={"requeued_units": len(abandoned)}, created_at=now,
            )
        return self.run_status(run_id)

    def verify_run(self, run_id: str) -> dict:
        status = self.run_status(run_id)
        incomplete = [
            unit["unit_id"] for unit in status["units"] if not unit["verified"]
        ]
        return {
            "run_id": run_id,
            "ok": status["coverage_complete"],
            "unit_count": status["unit_count"],
            "verified_completed": status["verified_completed"],
            "progress_percent": status["progress_percent"],
            "incomplete_unit_ids": incomplete,
            "issues": status["issues"],
        }

    def run_history(self, run_id: str) -> list[dict]:
        self._run_row(run_id)
        history = []
        for row in self.connection.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY sequence", (run_id,)
        ):
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            history.append(item)
        return history

    def revision(self, revision_uid: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM manuscript_revisions WHERE revision_uid=?", (revision_uid,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown revision: {revision_uid}")
        changes = {
            item[0]: item[1]
            for item in self.connection.execute(
                "SELECT change_type,COUNT(*) FROM paragraph_changes WHERE revision_uid=? GROUP BY change_type",
                (revision_uid,),
            )
        }
        payload = dict(row)
        payload["warnings"] = json.loads(payload.pop("warnings_json"))
        payload["metadata"] = json.loads(payload.pop("metadata_json"))
        payload["changes"] = changes
        payload["snapshot"] = str(self.root / payload["snapshot_path"])
        return payload

    def revision_diff(
        self,
        revision_uid: str,
        *,
        from_revision: str | None = None,
        include_text: bool = False,
    ) -> dict:
        revision = self.connection.execute(
            "SELECT revision_uid,parent_revision_uid FROM manuscript_revisions WHERE revision_uid=?",
            (revision_uid,),
        ).fetchone()
        if not revision:
            raise ValueError(f"Unknown revision: {revision_uid}")
        parent_uid = revision["parent_revision_uid"]
        if from_revision is not None and from_revision != parent_uid:
            raise ValueError(
                "Stored identity reconciliation is defined for a revision and its direct parent"
            )
        groups: list[dict] = []
        summary: dict[str, int] = {}
        unresolved = 0
        for row in self.connection.execute(
            """SELECT * FROM revision_diff_groups
               WHERE revision_uid=? ORDER BY entity_type,group_uid""",
            (revision_uid,),
        ):
            group = dict(row)
            group["semantic_signals"] = json.loads(group.pop("semantic_signals_json"))
            group["details"] = json.loads(group.pop("details_json"))
            group["confidence_label"] = confidence_label(group["confidence"])
            members = []
            for member_row in self.connection.execute(
                """SELECT side,entity_stable_id,paragraph_id,source_order,
                          chapter_stable_id,text_snapshot
                   FROM revision_diff_members WHERE group_uid=?
                   ORDER BY CASE side WHEN 'before' THEN 0 ELSE 1 END,source_order,id""",
                (group["group_uid"],),
            ):
                member = dict(member_row)
                member["stable_id"] = member.pop("entity_stable_id")
                if include_text:
                    member["text"] = member.pop("text_snapshot")
                else:
                    member.pop("text_snapshot")
                members.append(member)
            group["members"] = members
            group["candidates"] = [
                {
                    "candidate_uid": candidate["candidate_uid"],
                    "before_stable_ids": json.loads(candidate["before_stable_ids_json"]),
                    "after_stable_ids": json.loads(candidate["after_stable_ids_json"]),
                    "relation_type": candidate["relation_type"],
                    "confidence": candidate["confidence"],
                    "confidence_label": confidence_label(candidate["confidence"]),
                    "rank": candidate["rank"],
                    "signals": json.loads(candidate["signals_json"]),
                }
                for candidate in self.connection.execute(
                    "SELECT * FROM reconciliation_candidates WHERE group_uid=? ORDER BY rank,candidate_uid",
                    (group["group_uid"],),
                )
            ]
            event = self.connection.execute(
                """SELECT * FROM reconciliation_events WHERE group_uid=?
                   ORDER BY sequence DESC LIMIT 1""",
                (group["group_uid"],),
            ).fetchone()
            effective_relation = group["relation_type"]
            if event and event["action"] == "resolve":
                group["status"] = "manually_resolved"
                effective_relation = event["relation_type"]
                group["resolution"] = {
                    "event_uid": event["event_uid"],
                    "relation_type": event["relation_type"],
                    "before_stable_ids": json.loads(event["before_stable_ids_json"]),
                    "after_stable_ids": json.loads(event["after_stable_ids_json"]),
                    "actor": event["actor"],
                    "note": event["note"],
                    "created_at": event["created_at"],
                }
            elif event and event["action"] == "reopen":
                group["status"] = "needs_reconciliation"
                group["resolution"] = {
                    "event_uid": event["event_uid"], "action": "reopen",
                    "actor": event["actor"], "note": event["note"],
                    "created_at": event["created_at"],
                }
            group["effective_relation_type"] = effective_relation
            summary[effective_relation] = summary.get(effective_relation, 0) + 1
            if group["status"] == "needs_reconciliation":
                unresolved += 1
            groups.append(group)
        groups.sort(
            key=lambda group: (
                0 if group["entity_type"] == "chapter" else 1,
                min((member["source_order"] for member in group["members"]), default=10**12),
                group["group_uid"],
            )
        )
        return {
            "from_revision": parent_uid,
            "to_revision": revision_uid,
            "summary": summary,
            "unresolved_count": unresolved,
            "groups": groups,
            "semantic_notice": (
                "Name/number/negation fields are lexical risk signals only; "
                "authorial intent and semantic equivalence are not inferred."
            ),
        }

    def reconciliation_queue(self, revision_uid: str, *, include_text: bool = False) -> list[dict]:
        return [
            group for group in self.revision_diff(revision_uid, include_text=include_text)["groups"]
            if group["status"] == "needs_reconciliation"
        ]

    def _validate_reconciliation_shape(
        self, relation_type: str, before_ids: list[str], after_ids: list[str]
    ) -> None:
        allowed = {"unchanged", "changed", "moved", "split", "merged", "added", "removed"}
        if relation_type not in allowed:
            raise ValueError(f"Unsupported reconciliation relation: {relation_type}")
        shapes = {
            "unchanged": (1, 1), "changed": (1, 1), "moved": (1, 1),
            "added": (0, None), "removed": (None, 0),
        }
        if relation_type in shapes:
            expected_before, expected_after = shapes[relation_type]
            if expected_before is not None and len(before_ids) != expected_before:
                raise ValueError(f"{relation_type} requires {expected_before} before ID(s)")
            if expected_after is not None and len(after_ids) != expected_after:
                raise ValueError(f"{relation_type} requires {expected_after} after ID(s)")
            if relation_type == "added" and not after_ids:
                raise ValueError("added requires at least one after ID")
            if relation_type == "removed" and not before_ids:
                raise ValueError("removed requires at least one before ID")
        elif relation_type == "split" and not (len(before_ids) == 1 and len(after_ids) >= 2):
            raise ValueError("split requires one before ID and at least two after IDs")
        elif relation_type == "merged" and not (len(before_ids) >= 2 and len(after_ids) == 1):
            raise ValueError("merged requires at least two before IDs and one after ID")

    def _effective_mapping_claims(
        self, revision_uid: str, *, excluding_group_uid: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return active before/after ID owners for all other diff groups."""
        before_claims: dict[str, str] = {}
        after_claims: dict[str, str] = {}
        groups = self.connection.execute(
            """SELECT * FROM revision_diff_groups
               WHERE revision_uid=? AND group_uid<>? ORDER BY group_uid""",
            (revision_uid, excluding_group_uid),
        )
        for group in groups:
            event = self.connection.execute(
                """SELECT action,before_stable_ids_json,after_stable_ids_json
                   FROM reconciliation_events WHERE group_uid=?
                   ORDER BY sequence DESC LIMIT 1""",
                (group["group_uid"],),
            ).fetchone()
            if event:
                if event["action"] == "reopen":
                    continue
                before_ids = json.loads(event["before_stable_ids_json"])
                after_ids = json.loads(event["after_stable_ids_json"])
            elif group["status"] == "needs_reconciliation" or group["relation_type"] == "ambiguous":
                continue
            else:
                members = self.connection.execute(
                    """SELECT side,entity_stable_id FROM revision_diff_members
                       WHERE group_uid=? ORDER BY side,entity_stable_id""",
                    (group["group_uid"],),
                ).fetchall()
                before_ids = [row["entity_stable_id"] for row in members if row["side"] == "before"]
                after_ids = [row["entity_stable_id"] for row in members if row["side"] == "after"]
            for stable_id_value in before_ids:
                before_claims.setdefault(stable_id_value, group["group_uid"])
            for stable_id_value in after_ids:
                after_claims.setdefault(stable_id_value, group["group_uid"])
        return before_claims, after_claims

    def _validate_reconciliation_conflicts(
        self,
        revision_uid: str,
        group_uid: str,
        before_ids: list[str],
        after_ids: list[str],
    ) -> None:
        before_claims, after_claims = self._effective_mapping_claims(
            revision_uid, excluding_group_uid=group_uid
        )
        conflicts = [
            ("before", stable_id_value, before_claims[stable_id_value])
            for stable_id_value in before_ids if stable_id_value in before_claims
        ] + [
            ("after", stable_id_value, after_claims[stable_id_value])
            for stable_id_value in after_ids if stable_id_value in after_claims
        ]
        if conflicts:
            details = "; ".join(
                f"{side} {stable_id_value} is claimed by {owner}"
                for side, stable_id_value, owner in sorted(conflicts)
            )
            raise ValueError(f"Reconciliation conflicts with active mappings: {details}")

    def reconcile(
        self,
        revision_uid: str,
        group_uid: str,
        *,
        relation_type: str | None = None,
        before_ids: list[str] | None = None,
        after_ids: list[str] | None = None,
        candidate_uid: str | None = None,
        actor: str = "author",
        note: str | None = None,
    ) -> dict:
        group = self.connection.execute(
            "SELECT * FROM revision_diff_groups WHERE revision_uid=? AND group_uid=?",
            (revision_uid, group_uid),
        ).fetchone()
        if not group:
            raise ValueError(f"Unknown diff group for {revision_uid}: {group_uid}")
        if not actor.strip():
            raise ValueError("Reconciliation actor is required")
        if candidate_uid:
            candidate = self.connection.execute(
                """SELECT * FROM reconciliation_candidates
                   WHERE candidate_uid=? AND group_uid=?""",
                (candidate_uid, group_uid),
            ).fetchone()
            if not candidate:
                raise ValueError(f"Unknown candidate for group {group_uid}: {candidate_uid}")
            relation_type = candidate["relation_type"]
            before_ids = json.loads(candidate["before_stable_ids_json"])
            after_ids = json.loads(candidate["after_stable_ids_json"])
        before_ids = list(dict.fromkeys(before_ids or []))
        after_ids = list(dict.fromkeys(after_ids or []))
        if relation_type is None:
            raise ValueError("A relation type or candidate is required")
        self._validate_reconciliation_shape(relation_type, before_ids, after_ids)
        table = "paragraphs" if group["entity_type"] == "paragraph" else "chapters"
        valid_before = {
            row[0] for row in self.connection.execute(
                f"SELECT stable_id FROM {table} WHERE revision_uid=?",
                (group["parent_revision_uid"],),
            )
        } if group["parent_revision_uid"] else set()
        valid_after = {
            row[0] for row in self.connection.execute(
                f"SELECT stable_id FROM {table} WHERE revision_uid=?", (revision_uid,)
            )
        }
        unknown_before = set(before_ids) - valid_before
        unknown_after = set(after_ids) - valid_after
        if unknown_before or unknown_after:
            raise ValueError(
                f"Reconciliation IDs do not belong to the compared revisions: "
                f"before={sorted(unknown_before)}, after={sorted(unknown_after)}"
            )
        self._validate_reconciliation_conflicts(
            revision_uid, group_uid, before_ids, after_ids
        )
        previous = self.connection.execute(
            "SELECT event_uid FROM reconciliation_events WHERE group_uid=? ORDER BY sequence DESC LIMIT 1",
            (group_uid,),
        ).fetchone()
        event_uid = stable_id("rec")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO reconciliation_events(
                    event_uid,group_uid,action,relation_type,before_stable_ids_json,
                    after_stable_ids_json,actor,note,created_at,supersedes_event_uid
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_uid, group_uid, "resolve", relation_type,
                    json.dumps(before_ids, ensure_ascii=False),
                    json.dumps(after_ids, ensure_ascii=False), actor, note, now,
                    previous[0] if previous else None,
                ),
            )
            self.connection.execute(
                "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    "reconciliation.resolved", revision_uid,
                    json.dumps({"group_uid": group_uid, "event_uid": event_uid, "actor": actor}), now,
                ),
            )
        return next(
            item for item in self.revision_diff(revision_uid)["groups"]
            if item["group_uid"] == group_uid
        )

    def reopen_reconciliation(
        self, revision_uid: str, group_uid: str, *, actor: str = "author", note: str | None = None
    ) -> dict:
        group = self.connection.execute(
            "SELECT 1 FROM revision_diff_groups WHERE revision_uid=? AND group_uid=?",
            (revision_uid, group_uid),
        ).fetchone()
        if not group:
            raise ValueError(f"Unknown diff group for {revision_uid}: {group_uid}")
        if not actor.strip():
            raise ValueError("Reconciliation actor is required")
        previous = self.connection.execute(
            "SELECT event_uid FROM reconciliation_events WHERE group_uid=? ORDER BY sequence DESC LIMIT 1",
            (group_uid,),
        ).fetchone()
        event_uid = stable_id("rec")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO reconciliation_events(
                    event_uid,group_uid,action,relation_type,before_stable_ids_json,
                    after_stable_ids_json,actor,note,created_at,supersedes_event_uid
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_uid, group_uid, "reopen", None, "[]", "[]", actor, note, now,
                    previous[0] if previous else None,
                ),
            )
            self.connection.execute(
                "INSERT INTO audit_log(event_type,revision_uid,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    "reconciliation.reopened", revision_uid,
                    json.dumps({"group_uid": group_uid, "event_uid": event_uid, "actor": actor}), now,
                ),
            )
        return next(
            item for item in self.revision_diff(revision_uid)["groups"]
            if item["group_uid"] == group_uid
        )

    def reconciliation_history(self, revision_uid: str, group_uid: str | None = None) -> list[dict]:
        parameters: list[str] = [revision_uid]
        where = "g.revision_uid=?"
        if group_uid:
            where += " AND e.group_uid=?"
            parameters.append(group_uid)
        rows = self.connection.execute(
            f"""SELECT e.* FROM reconciliation_events e
                JOIN revision_diff_groups g ON g.group_uid=e.group_uid
                WHERE {where} ORDER BY e.sequence""",
            parameters,
        )
        history = []
        for row in rows:
            event = dict(row)
            event["before_stable_ids"] = json.loads(event.pop("before_stable_ids_json"))
            event["after_stable_ids"] = json.loads(event.pop("after_stable_ids_json"))
            history.append(event)
        return history

    def revisions(self) -> list[dict]:
        return [self.revision(row[0]) for row in self.connection.execute(
            "SELECT revision_uid FROM manuscript_revisions ORDER BY id"
        )]

    def status(self) -> dict:
        project = dict(self.connection.execute("SELECT * FROM projects LIMIT 1").fetchone())
        latest = self.connection.execute(
            "SELECT revision_uid FROM manuscript_revisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        project["metadata"] = json.loads(project.pop("metadata_json"))
        return {
            "project": project,
            "schema_version": self.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            "latest_revision": latest[0] if latest else None,
            "revision_count": self.connection.execute("SELECT COUNT(*) FROM manuscript_revisions").fetchone()[0],
            "decision_counts": {
                row[0]: row[1]
                for row in self.connection.execute("SELECT status,COUNT(*) FROM author_decisions GROUP BY status")
            },
            "finding_count": self.connection.execute("SELECT COUNT(*) FROM editorial_findings").fetchone()[0],
            "unresolved_reconciliation_count": self.connection.execute(
                """SELECT COUNT(*) FROM revision_diff_groups g
                   WHERE g.status='needs_reconciliation'
                   AND NOT EXISTS(
                       SELECT 1 FROM reconciliation_events e WHERE e.group_uid=g.group_uid
                       AND e.sequence=(SELECT MAX(e2.sequence) FROM reconciliation_events e2 WHERE e2.group_uid=g.group_uid)
                       AND e.action='resolve'
                   )"""
            ).fetchone()[0],
            "correction_counts": {
                row[0]: row[1]
                for row in self.connection.execute(
                    "SELECT status,COUNT(*) FROM corrections GROUP BY status"
                )
            },
            "application_count": self.connection.execute(
                "SELECT COUNT(*) FROM change_applications"
            ).fetchone()[0],
            "run_counts": {
                row[0]: row[1]
                for row in self.connection.execute(
                    """SELECT r.status,COUNT(*) FROM runs r
                       WHERE EXISTS(SELECT 1 FROM run_units u WHERE u.run_id=r.run_id)
                       GROUP BY r.status"""
                )
            },
            "legacy_unplanned_run_count": self.connection.execute(
                """SELECT COUNT(*) FROM runs r
                   WHERE NOT EXISTS(SELECT 1 FROM run_units u WHERE u.run_id=r.run_id)"""
            ).fetchone()[0],
            "book_map_count": self.connection.execute(
                "SELECT COUNT(*) FROM book_maps"
            ).fetchone()[0],
        }

    def verify(self) -> dict:
        results: list[dict] = []
        for revision in self.revisions():
            issues: list[str] = []
            relative = _safe_project_relative(revision["snapshot_path"])
            path = self.root / relative if relative else None
            if path is None:
                issues.append("unsafe snapshot path")
            elif not path.is_file():
                issues.append("snapshot missing")
            else:
                if path.stat().st_size != revision["source_size"]:
                    issues.append("snapshot size changed")
                if sha256_file(path) != revision["source_sha256"]:
                    issues.append("snapshot SHA-256 mismatch")
            actual_paragraphs = self.connection.execute(
                "SELECT COUNT(*) FROM paragraphs WHERE revision_uid=?", (revision["revision_uid"],)
            ).fetchone()[0]
            actual_chapters = self.connection.execute(
                "SELECT COUNT(*) FROM chapters WHERE revision_uid=?", (revision["revision_uid"],)
            ).fetchone()[0]
            if actual_paragraphs != revision["paragraph_count"]:
                issues.append("paragraph completeness mismatch")
            if actual_chapters != revision["chapter_count"]:
                issues.append("chapter completeness mismatch")
            for correction in self.connection.execute(
                "SELECT * FROM corrections WHERE source_revision_uid=?",
                (revision["revision_uid"],),
            ):
                paragraph = self.connection.execute(
                    """SELECT text FROM paragraphs
                       WHERE revision_uid=? AND stable_id=?""",
                    (revision["revision_uid"], correction["paragraph_stable_id"]),
                ).fetchone()
                if not paragraph:
                    issues.append(f"correction target missing: {correction['correction_id']}")
                    continue
                start = correction["start_offset"]
                end = correction["end_offset"]
                if start is None or end is None or paragraph[0][start:end] != correction["exact_before"]:
                    issues.append(f"correction exact span mismatch: {correction['correction_id']}")
            for entity_type, table in (("paragraph", "paragraphs"), ("chapter", "chapters")):
                expected_after = {
                    row[0] for row in self.connection.execute(
                        f"SELECT stable_id FROM {table} WHERE revision_uid=?",
                        (revision["revision_uid"],),
                    )
                }
                member_after = [
                    row[0] for row in self.connection.execute(
                        """SELECT m.entity_stable_id
                           FROM revision_diff_members m
                           JOIN revision_diff_groups g ON g.group_uid=m.group_uid
                           WHERE g.revision_uid=? AND g.entity_type=? AND m.side='after'""",
                        (revision["revision_uid"], entity_type),
                    )
                ]
                if set(member_after) != expected_after or len(member_after) != len(expected_after):
                    issues.append(f"{entity_type} diff after-side completeness mismatch")

                parent_uid = revision["parent_revision_uid"]
                if parent_uid:
                    expected_before = {
                        row[0] for row in self.connection.execute(
                            f"SELECT stable_id FROM {table} WHERE revision_uid=?", (parent_uid,)
                        )
                    }
                    represented_before = {
                        row[0] for row in self.connection.execute(
                            """SELECT m.entity_stable_id
                               FROM revision_diff_members m
                               JOIN revision_diff_groups g ON g.group_uid=m.group_uid
                               WHERE g.revision_uid=? AND g.entity_type=? AND m.side='before'""",
                            (revision["revision_uid"], entity_type),
                        )
                    }
                    for candidate in self.connection.execute(
                        """SELECT c.before_stable_ids_json
                           FROM reconciliation_candidates c
                           JOIN revision_diff_groups g ON g.group_uid=c.group_uid
                           WHERE g.revision_uid=? AND g.entity_type=?""",
                        (revision["revision_uid"], entity_type),
                    ):
                        represented_before.update(json.loads(candidate[0]))
                    if represented_before != expected_before:
                        issues.append(f"{entity_type} diff before-side completeness mismatch")
            if revision["content_map_hash"]:
                rows = self.connection.execute(
                    """SELECT source_order,text,style_json,runs_json,source_ref_json
                       FROM paragraphs WHERE revision_uid=? ORDER BY source_order""",
                    (revision["revision_uid"],),
                )
                content_payload = [
                    {
                        "order": row["source_order"],
                        "text": row["text"],
                        "style": json.loads(row["style_json"]),
                        "runs": json.loads(row["runs_json"]),
                        "source_ref": json.loads(row["source_ref_json"]),
                    }
                    for row in rows
                ]
                actual_map_hash = sha256_bytes(
                    json.dumps(
                        content_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                )
                if actual_map_hash != revision["content_map_hash"]:
                    issues.append("structured content-map hash mismatch")
            application = self.connection.execute(
                "SELECT * FROM change_applications WHERE result_revision_uid=?",
                (revision["revision_uid"],),
            ).fetchone()
            if application:
                if revision["parent_revision_uid"] != application["source_revision_uid"]:
                    issues.append("application parent revision mismatch")
                application_source = self.connection.execute(
                    "SELECT source_sha256 FROM manuscript_revisions WHERE revision_uid=?",
                    (application["source_revision_uid"],),
                ).fetchone()
                if not application_source or application_source[0] != application["source_sha256"]:
                    issues.append("application source SHA-256 mismatch")
            for book_map in self.connection.execute(
                "SELECT * FROM book_maps WHERE revision_uid=?", (revision["revision_uid"],)
            ):
                if book_map["source_sha256"] != revision["source_sha256"]:
                    issues.append(f"book map source SHA-256 mismatch: {book_map['map_run_id']}")
                for json_column in ("coverage_json", "quality_json"):
                    try:
                        value = json.loads(book_map[json_column])
                    except (TypeError, json.JSONDecodeError):
                        value = None
                    if not isinstance(value, dict):
                        issues.append(
                            f"book map invalid {json_column}: {book_map['map_run_id']}"
                        )
                for pass_run_id in (book_map["pass_a_run_id"], book_map["pass_b_run_id"]):
                    try:
                        pass_status = self.run_status(pass_run_id)
                    except ValueError:
                        pass_status = None
                    if pass_status is None or not pass_status["coverage_complete"]:
                        issues.append(
                            f"book map pass coverage incomplete: {book_map['map_run_id']}:{pass_run_id}"
                        )
                for stored_record in self.connection.execute(
                    "SELECT * FROM book_map_records WHERE map_run_id=? ORDER BY record_id",
                    (book_map["map_run_id"],),
                ):
                    try:
                        record_value = json.loads(stored_record["record_json"])
                    except (TypeError, json.JSONDecodeError):
                        record_value = None
                    expected_fields = {
                        "id": stored_record["record_id"],
                        "kind": stored_record["kind"],
                        "record_class": stored_record["record_class"],
                        "epistemic_status": stored_record["epistemic_status"],
                        "scope": stored_record["scope"],
                        "narrative_frame": stored_record["narrative_frame"],
                        "statement": stored_record["statement"],
                    }
                    if not isinstance(record_value, dict) or any(
                        record_value.get(key) != value for key, value in expected_fields.items()
                    ):
                        issues.append(
                            f"book map record payload mismatch: {book_map['map_run_id']}:{stored_record['record_id']}"
                        )
                        continue
                    stored_evidence = [
                        {
                            "revision_uid": row["revision_uid"],
                            "paragraph_stable_id": row["paragraph_stable_id"],
                            "exact_quote": row["exact_quote"],
                            **(
                                {"start_offset": row["start_offset"], "end_offset": row["end_offset"]}
                                if row["start_offset"] is not None or row["end_offset"] is not None
                                else {}
                            ),
                        }
                        for row in self.connection.execute(
                            """SELECT * FROM book_map_evidence
                               WHERE map_run_id=? AND record_id=?
                               ORDER BY evidence_order""",
                            (book_map["map_run_id"], stored_record["record_id"]),
                        )
                    ]
                    if record_value.get("evidence") != stored_evidence:
                        issues.append(
                            f"book map record/evidence mismatch: {book_map['map_run_id']}:{stored_record['record_id']}"
                        )
                evidence_rows = self.connection.execute(
                    """SELECT e.*,p.text
                       FROM book_map_evidence e
                       LEFT JOIN paragraphs p
                         ON p.revision_uid=e.revision_uid
                        AND p.stable_id=e.paragraph_stable_id
                       WHERE e.map_run_id=?
                       ORDER BY e.record_id,e.evidence_order""",
                    (book_map["map_run_id"],),
                )
                for evidence in evidence_rows:
                    if evidence["revision_uid"] != revision["revision_uid"] or evidence["text"] is None:
                        issues.append(
                            f"book map evidence target missing: {book_map['map_run_id']}:{evidence['record_id']}"
                        )
                        continue
                    start, end = evidence["start_offset"], evidence["end_offset"]
                    quote = evidence["exact_quote"]
                    if (start is None) != (end is None):
                        issues.append(
                            f"book map evidence offsets incomplete: {book_map['map_run_id']}:{evidence['record_id']}"
                        )
                    elif start is not None and evidence["text"][start:end] != quote:
                        issues.append(
                            f"book map evidence exact span mismatch: {book_map['map_run_id']}:{evidence['record_id']}"
                        )
                    elif start is None and quote not in evidence["text"]:
                        issues.append(
                            f"book map evidence quote mismatch: {book_map['map_run_id']}:{evidence['record_id']}"
                        )
            results.append({"revision_uid": revision["revision_uid"], "ok": not issues, "issues": issues})
        runs = [
            self.verify_run(row[0])
            for row in self.connection.execute(
                """SELECT r.run_id FROM runs r
                   WHERE EXISTS(SELECT 1 FROM run_units u WHERE u.run_id=r.run_id)
                   ORDER BY r.created_at,r.run_id"""
            )
        ]
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {
            "ok": (
                integrity == "ok"
                and all(item["ok"] for item in results)
                and all(item["ok"] for item in runs)
            ),
            "database": integrity,
            "revisions": results,
            "runs": runs,
        }
