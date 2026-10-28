#!/usr/bin/env python3
"""Prepare, resume, and validate local full-manuscript editing runs.

This tool never calls a network service. It supports UTF-8 TXT/Markdown and
ordinary DOCX files using only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


VERSION = "1.0.0"
SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".docx"}
HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|(?:chapter|part|book|prologue|epilogue|introduction|conclusion)\b.*|"
    r"(?:глава|часть|книга|пролог|эпилог|введение|заключение)\b.*|"
    r"(?:cap[ií]tulo|parte|pr[oó]logo|ep[ií]logo)\b.*|"
    r"(?:chapitre|partie|prologue|[eé]pilogue)\b.*|"
    r"(?:kapitel|teil|prolog|epilog)\b.*)$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


@dataclass
class SourceLine:
    text: str
    heading: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode {path}; save it as UTF-8, UTF-16, or DOCX")


def extract_docx(path: Path) -> list[SourceLine]:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX is missing word/document.xml") from exc
    root = ElementTree.fromstring(xml)
    lines: list[SourceLine] = []
    for paragraph in root.iter(f"{{{namespace}}}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{namespace}}}t")).strip()
        if not text:
            continue
        style = ""
        properties = paragraph.find(f"{{{namespace}}}pPr")
        if properties is not None:
            style_node = properties.find(f"{{{namespace}}}pStyle")
            if style_node is not None:
                style = style_node.attrib.get(f"{{{namespace}}}val", "")
        is_heading = style.lower().startswith(("heading", "title")) or bool(HEADING_RE.match(text))
        lines.append(SourceLine(text=text, heading=is_heading))
    return lines


def extract_source(path: Path) -> list[SourceLine]:
    if path.suffix.lower() == ".docx":
        lines = extract_docx(path)
    else:
        lines = [SourceLine(text=line.rstrip(), heading=bool(HEADING_RE.match(line.strip())))
                 for line in decode_text(path).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        lines = [line for line in lines if line.text.strip()]
    if not lines:
        raise ValueError("The manuscript contains no readable text")
    return lines


def split_chapters(lines: list[SourceLine]) -> list[tuple[str, list[SourceLine]]]:
    heading_indexes = [index for index, line in enumerate(lines) if line.heading]
    if not heading_indexes:
        return [("Manuscript", lines)]
    chapters: list[tuple[str, list[SourceLine]]] = []
    if heading_indexes[0] > 0:
        chapters.append(("Front matter", lines[:heading_indexes[0]]))
    for position, start in enumerate(heading_indexes):
        end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        title = re.sub(r"^#{1,6}\s+", "", lines[start].text).strip()
        chapters.append((title, lines[start:end]))
    return [(title, body) for title, body in chapters if any(line.text.strip() for line in body)]


def chunk_chapter(lines: list[SourceLine], max_words: int) -> list[list[SourceLine]]:
    chunks: list[list[SourceLine]] = []
    current: list[SourceLine] = []
    current_words = 0
    for line in lines:
        size = max(1, word_count(line.text))
        if current and current_words + size > max_words:
            chunks.append(current)
            current = []
            current_words = 0
        current.append(line)
        current_words += size
    if current:
        chunks.append(current)
    return chunks


def render_chunk(chunk_id: str, chapter_id: str, title: str, part: int, lines: list[SourceLine]) -> str:
    header = [
        "---",
        f"chunk_id: {chunk_id}",
        f"chapter_id: {chapter_id}",
        f"chapter_title: {json.dumps(title, ensure_ascii=False)}",
        f"part: {part}",
        "---",
        "",
        "Use the numbered lines for citations. Do not include the number prefix in a quoted passage.",
        "",
    ]
    width = max(4, len(str(len(lines))))
    body = [f"{index:0{width}d} | {line.text}" for index, line in enumerate(lines, 1)]
    return "\n".join(header + body) + "\n"


def prepare(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    workspace = args.workspace.resolve()
    if not source.is_file():
        raise ValueError(f"Source file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Supported inputs are TXT, Markdown, and DOCX")
    if args.max_words < 500:
        raise ValueError("--max-words must be at least 500")
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(f"Workspace is not empty: {workspace}. Choose a new directory to preserve existing work.")

    source_lines = extract_source(source)
    chapters = split_chapters(source_lines)
    for folder in ("input", "work/chunks", "work/chapter-reports", "output"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    copied_source = workspace / "input" / source.name
    shutil.copy2(source, copied_source)
    (workspace / "work" / "extracted.txt").write_text(
        "\n".join(line.text for line in source_lines) + "\n", encoding="utf-8"
    )

    manifest_chunks: list[dict] = []
    manifest_chapters: list[dict] = []
    total_words = 0
    for chapter_number, (title, chapter_lines) in enumerate(chapters, 1):
        chapter_id = f"ch{chapter_number:03d}"
        parts = chunk_chapter(chapter_lines, args.max_words)
        chapter_word_count = sum(word_count(line.text) for line in chapter_lines)
        total_words += chapter_word_count
        chapter_chunk_ids: list[str] = []
        for part_number, part_lines in enumerate(parts, 1):
            chunk_id = f"{chapter_id}-p{part_number:03d}"
            chunk_path = workspace / "work" / "chunks" / f"{chunk_id}.md"
            chunk_path.write_text(
                render_chunk(chunk_id, chapter_id, title, part_number, part_lines), encoding="utf-8"
            )
            chapter_chunk_ids.append(chunk_id)
            manifest_chunks.append({
                "id": chunk_id,
                "chapter_id": chapter_id,
                "chapter_title": title,
                "part": part_number,
                "file": str(chunk_path.relative_to(workspace)).replace("\\", "/"),
                "line_count": len(part_lines),
                "word_count": sum(word_count(line.text) for line in part_lines),
            })
        manifest_chapters.append({
            "id": chapter_id,
            "title": title,
            "word_count": chapter_word_count,
            "chunks": chapter_chunk_ids,
        })

    manifest = {
        "schema_version": 1,
        "workflow_version": VERSION,
        "mode": args.mode,
        "source_file": str(copied_source.relative_to(workspace)).replace("\\", "/"),
        "source_name": source.name,
        "prepared_at": utc_now(),
        "word_count": total_words,
        "chapter_count": len(manifest_chapters),
        "chunk_count": len(manifest_chunks),
        "max_words_per_chunk": args.max_words,
        "chapters": manifest_chapters,
        "chunks": manifest_chunks,
    }
    state = {
        "schema_version": 1,
        "mode": args.mode,
        "updated_at": utc_now(),
        "chunks": {chunk["id"]: {"status": "pending", "report": None} for chunk in manifest_chunks},
    }
    write_json(workspace / "work" / "manifest.json", manifest)
    write_json(workspace / "work" / "state.json", state)
    (workspace / "work" / "evidence.jsonl").write_text("", encoding="utf-8")
    book_map = (
        f"# Book map: {source.stem}\n\n"
        f"Mode: {args.mode}\n\n"
        f"Prepared inventory: {len(manifest_chapters)} chapters, {len(manifest_chunks)} chunks, "
        f"{total_words:,} words.\n\n"
        "Complete the sections in the bundled book-map template before chapter-level conclusions.\n"
    )
    (workspace / "work" / "book-map.md").write_text(book_map, encoding="utf-8")
    print(f"Prepared {total_words:,} words in {len(manifest_chapters)} chapters / {len(manifest_chunks)} chunks")
    print(f"Workspace: {workspace}")
    return 0


def load_workspace(workspace: Path) -> tuple[dict, dict]:
    manifest_path = workspace / "work" / "manifest.json"
    state_path = workspace / "work" / "state.json"
    if not manifest_path.is_file() or not state_path.is_file():
        raise ValueError(f"Not an EditRoast workspace: {workspace}")
    return read_json(manifest_path), read_json(state_path)


def show_status(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    manifest, state = load_workspace(workspace)
    completed = [key for key, value in state["chunks"].items() if value.get("status") == "completed"]
    pending = [key for key, value in state["chunks"].items() if value.get("status") != "completed"]
    stale = []
    for chunk_id in completed:
        report = state["chunks"][chunk_id].get("report")
        if not report or not (workspace / report).is_file():
            stale.append(chunk_id)
    print(f"Mode: {manifest['mode']}")
    print(f"Progress: {len(completed)}/{manifest['chunk_count']} chunks complete")
    if pending:
        print("Next pending: " + ", ".join(pending[:10]))
    if stale:
        print("Missing report files: " + ", ".join(stale))
        return 1
    return 0


def mark_complete(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    _, state = load_workspace(workspace)
    if args.chunk_id not in state["chunks"]:
        raise ValueError(f"Unknown chunk ID: {args.chunk_id}")
    report = args.report.resolve()
    try:
        relative = report.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Report must be inside the workspace") from exc
    if not report.is_file() or report.stat().st_size == 0:
        raise ValueError(f"Report is missing or empty: {report}")
    state["chunks"][args.chunk_id] = {
        "status": "completed",
        "report": str(relative).replace("\\", "/"),
        "completed_at": utc_now(),
    }
    state["updated_at"] = utc_now()
    write_json(workspace / "work" / "state.json", state)
    print(f"Marked {args.chunk_id} complete")
    return 0


def parse_chunk_lines(path: Path) -> list[str]:
    numbered: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\d+ \| (.*)$", line)
        if match:
            numbered.append(match.group(1))
    return numbered


def normalize_quote(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_evidence(workspace: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    chunks = {item["id"]: item for item in manifest["chunks"]}
    evidence_path = workspace / "work" / "evidence.jsonl"
    if not evidence_path.is_file():
        return ["Missing work/evidence.jsonl"]
    for number, raw in enumerate(evidence_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"evidence line {number}: invalid JSON ({exc.msg})")
            continue
        missing = [key for key in ("finding_id", "chunk_id", "start_line", "end_line", "quote") if key not in item]
        if missing:
            errors.append(f"evidence line {number}: missing {', '.join(missing)}")
            continue
        chunk = chunks.get(str(item["chunk_id"]))
        if not chunk:
            errors.append(f"evidence line {number}: unknown chunk {item['chunk_id']}")
            continue
        lines = parse_chunk_lines(workspace / chunk["file"])
        start, end = item["start_line"], item["end_line"]
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines):
            errors.append(f"evidence line {number}: invalid line range {start}-{end}")
            continue
        source_text = normalize_quote("\n".join(lines[start - 1:end]))
        quote = normalize_quote(str(item["quote"]))
        if not quote or quote not in source_text:
            errors.append(f"evidence line {number}: quote does not match {item['chunk_id']}:{start}-{end}")
    return errors


def validate(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    manifest, state = load_workspace(workspace)
    errors: list[str] = []
    manifest_ids = [item["id"] for item in manifest.get("chunks", [])]
    state_ids = list(state.get("chunks", {}))
    if manifest.get("chunk_count") != len(manifest_ids):
        errors.append("manifest chunk_count does not match chunks list")
    if set(manifest_ids) != set(state_ids):
        errors.append("state chunk IDs do not match manifest")
    for item in manifest.get("chunks", []):
        chunk_path = workspace / item["file"]
        if not chunk_path.is_file():
            errors.append(f"missing chunk file: {item['file']}")
        status = state.get("chunks", {}).get(item["id"], {})
        if status.get("status") != "completed":
            errors.append(f"incomplete chunk: {item['id']}")
        else:
            report = status.get("report")
            if not report or not (workspace / report).is_file():
                errors.append(f"completed chunk has no report: {item['id']}")
    errors.extend(validate_evidence(workspace, manifest))
    final_name = "manuscript-roast-report.md" if manifest.get("mode") == "roast" else "proofreading-report.md"
    if args.require_final and not (workspace / "output" / final_name).is_file():
        errors.append(f"missing final report: output/{final_name}")
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Validation passed: all {len(manifest_ids)} chunks complete; evidence references are valid")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Create a new local manuscript workspace")
    prepare_parser.add_argument("source", type=Path)
    prepare_parser.add_argument("--workspace", type=Path, required=True)
    prepare_parser.add_argument("--mode", choices=("roast", "proofreading"), required=True)
    prepare_parser.add_argument("--max-words", type=int, default=5000)
    prepare_parser.set_defaults(func=prepare)

    status_parser = subparsers.add_parser("status", help="Show resumable progress")
    status_parser.add_argument("--workspace", type=Path, required=True)
    status_parser.set_defaults(func=show_status)

    complete_parser = subparsers.add_parser("complete", help="Mark one chunk complete after its report exists")
    complete_parser.add_argument("--workspace", type=Path, required=True)
    complete_parser.add_argument("--chunk-id", required=True)
    complete_parser.add_argument("--report", type=Path, required=True)
    complete_parser.set_defaults(func=mark_complete)

    validate_parser = subparsers.add_parser("validate", help="Verify completeness and exact evidence quotes")
    validate_parser.add_argument("--workspace", type=Path, required=True)
    validate_parser.add_argument("--require-final", action="store_true")
    validate_parser.set_defaults(func=validate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
