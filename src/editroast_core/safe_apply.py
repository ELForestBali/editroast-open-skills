"""Deterministic local rendering helpers for author-approved corrections.

This module only edits a staged copy. The project layer verifies approvals,
source hashes, exact spans, and commits the result as a new immutable revision.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _safe_xml(data: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Unsafe XML declaration in word/document.xml")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError(f"Malformed word/document.xml: {error}") from error


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


def _run_text(run: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in run.iter():
        if node.tag == f"{{{W_NS}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{W_NS}}}tab":
            parts.append("\t")
        elif node.tag in {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}:
            parts.append("\n")
    return "".join(parts)


def _docx_patch_targets(
    source: Path, patches: list[dict]
) -> tuple[ElementTree.Element, dict[str, tuple[ElementTree.Element, int]]]:
    with zipfile.ZipFile(source) as archive:
        try:
            root = _safe_xml(archive.read("word/document.xml"))
        except KeyError as error:
            raise ValueError("DOCX is missing word/document.xml") from error
    paragraphs = list(root.iter(f"{{{W_NS}}}p"))
    targets: dict[str, tuple[ElementTree.Element, int]] = {}
    for patch in patches:
        if any(character in patch["after"] for character in "\t\r\n"):
            raise ValueError(
                "Formatting collision for correction "
                f"{patch['correction_id']}: DOCX control whitespace requires explicit run markup; "
                "edit this passage manually"
            )
        order = patch["source_order"]
        if order >= len(paragraphs):
            raise ValueError(
                f"DOCX paragraph {order} is missing for correction {patch['correction_id']}"
            )
        runs = _paragraph_runs(paragraphs[order])
        cursor = 0
        selected: tuple[ElementTree.Element, int] | None = None
        for run in runs:
            run_text = _run_text(run)
            run_end = cursor + len(run_text)
            if patch["start"] >= cursor and patch["end"] <= run_end:
                text_nodes = list(run.iter(f"{{{W_NS}}}t"))
                unsupported = any(
                    node.tag in {f"{{{W_NS}}}tab", f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}
                    for node in run.iter()
                )
                if len(text_nodes) != 1 or unsupported or (text_nodes[0].text or "") != run_text:
                    raise ValueError(
                        "Formatting collision for correction "
                        f"{patch['correction_id']}: target run contains multiple text/control nodes; "
                        "split the correction or edit this passage manually"
                    )
                selected = text_nodes[0], cursor
                break
            cursor = run_end
        if selected is None:
            raise ValueError(
                "Formatting collision for correction "
                f"{patch['correction_id']}: exact span crosses DOCX runs; "
                "split the correction by formatting run or edit this passage manually"
            )
        targets[patch["correction_id"]] = selected
    return root, targets


def renderability_errors(source: Path, source_format: str, patches: list[dict]) -> list[str]:
    if source_format != "docx":
        return []
    try:
        _docx_patch_targets(source, patches)
    except (ValueError, zipfile.BadZipFile, OSError) as error:
        return [str(error)]
    return []


def _decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Text cannot be decoded as UTF-8, UTF-16, or Windows-1252")


def _render_text(source: Path, destination: Path, patches: list[dict]) -> None:
    decoded, encoding = _decode_text(source.read_bytes())
    lines = decoded.splitlines(keepends=True)
    if not lines and decoded == "":
        lines = [""]
    grouped: dict[int, list[dict]] = {}
    for patch in patches:
        grouped.setdefault(patch["source_order"], []).append(patch)
    for order, values in grouped.items():
        if order >= len(lines):
            raise ValueError(f"Text line {order + 1} is missing during apply")
        match = re.match(r"^(.*?)(\r\n|\n|\r)?$", lines[order], re.DOTALL)
        body = match.group(1) if match else lines[order]
        ending = match.group(2) or "" if match else ""
        for patch in sorted(values, key=lambda item: item["start"], reverse=True):
            if body[patch["start"]:patch["end"]] != patch["before"]:
                raise ValueError(
                    f"Exact source span changed for correction {patch['correction_id']}"
                )
            body = body[:patch["start"]] + patch["after"] + body[patch["end"]:]
        lines[order] = body + ending
    data = "".join(lines).encode(encoding)
    with destination.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _render_docx(source: Path, destination: Path, patches: list[dict]) -> None:
    root, targets = _docx_patch_targets(source, patches)
    by_node: dict[int, tuple[ElementTree.Element, int, list[dict]]] = {}
    for patch in patches:
        node, run_start = targets[patch["correction_id"]]
        key = id(node)
        if key not in by_node:
            by_node[key] = node, run_start, []
        by_node[key][2].append(patch)
    for node, run_start, values in by_node.values():
        text = node.text or ""
        for patch in sorted(values, key=lambda item: item["start"], reverse=True):
            start = patch["start"] - run_start
            end = patch["end"] - run_start
            if text[start:end] != patch["before"]:
                raise ValueError(
                    f"Exact DOCX run span changed for correction {patch['correction_id']}"
                )
            text = text[:start] + patch["after"] + text[end:]
        node.text = text
        if text[:1].isspace() or text[-1:].isspace():
            node.set(f"{{{XML_NS}}}space", "preserve")
        else:
            node.attrib.pop(f"{{{XML_NS}}}space", None)
    ElementTree.register_namespace("w", W_NS)
    document_xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as rendered:
        for info in original.infolist():
            data = document_xml if info.filename == "word/document.xml" else original.read(info)
            rendered.writestr(info, data)


def render_staged_revision(
    source: Path, destination: Path, source_format: str, patches: list[dict]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source_format == "docx":
        _render_docx(source, destination, patches)
    elif source_format in {"text", "markdown"}:
        _render_text(source, destination, patches)
    else:
        raise ValueError(f"Safe apply is not supported for source format: {source_format}")
