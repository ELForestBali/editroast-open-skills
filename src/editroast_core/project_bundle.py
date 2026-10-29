"""Portable, integrity-checked EditRoast project bundles."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .manuscript_state import (
    SCHEMA_VERSION,
    ManuscriptProject,
    _safe_project_relative,
    sha256_file,
    utc_now,
)


BUNDLE_FORMAT = "editroast-project"
BUNDLE_VERSION = 1
MAX_BUNDLE_MEMBERS = 10_000
MAX_BUNDLE_MEMBER_SIZE = 512 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
MAX_BUNDLE_COMPRESSION_RATIO = 200
FIXED_ZIP_TIME = (2026, 10, 28, 0, 0, 0)
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "password",
    "secret",
    "client_secret",
    "credential",
    "credentials",
    "authorization",
}
SECRET_RE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|"
    rb"github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY-----)",
    re.IGNORECASE,
)
PRIVATE_PATH_RE = re.compile(
    rb"(?:[A-Z]:[\\/][^\x00\r\n\t ]+|"
    rb"/(?:home|Users|private|tmp|var/folders)/[^\x00\r\n\t ]+)",
    re.IGNORECASE,
)
JSON_COLUMNS = {
    "projects": ("metadata_json",),
    "manuscript_revisions": ("warnings_json", "metadata_json"),
    "paragraph_changes": ("details_json",),
    "editorial_findings": ("anchor_json",),
    "author_decisions": ("payload_json", "selected_json"),
    "decision_events": ("selected_json",),
    "runs": ("provenance_json",),
    "audit_log": ("payload_json",),
    "revision_diff_groups": ("semantic_signals_json", "details_json"),
    "reconciliation_candidates": ("evidence_json",),
    "run_events": ("payload_json",),
    "book_maps": ("coverage_json", "quality_json"),
    "editorial_board_reviews": ("verdict_json", "decision_json"),
    "editorial_board_groups": ("group_json", "correction_ids_json"),
    "editorial_board_events": ("payload_json",),
    "editorial_board_rechecks": ("verdict_json", "report_json"),
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _redact_string(value: str, roots: tuple[str, ...]) -> tuple[str, int]:
    redactions = 0
    updated = value
    if SECRET_RE.search(updated.encode("utf-8", errors="ignore")):
        updated = SECRET_RE.sub(b"[REDACTED_SECRET]", updated.encode("utf-8")).decode("utf-8")
        redactions += 1
    normalized = updated.replace("\\", "/")
    for root in roots:
        if not root:
            continue
        candidates = {root, root.replace("\\", "/"), root.replace("/", "\\")}
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate and candidate in updated:
                updated = updated.replace(candidate, "[REDACTED_PATH]")
                redactions += 1
        normalized = updated.replace("\\", "/")
    if (
        PureWindowsPath(updated).is_absolute()
        or PurePosixPath(normalized).is_absolute()
    ):
        updated = "[REDACTED_PATH]"
        redactions += 1
    return updated, redactions


def _sanitize_value(value: object, roots: tuple[str, ...], key: str | None = None) -> tuple[object, int]:
    normalized_key = key.lower().replace("-", "_") if key else ""
    sensitive_key = normalized_key in SENSITIVE_KEYS or normalized_key.endswith(
        ("_api_key", "_access_token", "_refresh_token", "_auth_token", "_password", "_secret", "_credential", "_credentials")
    )
    if sensitive_key and value not in (None, ""):
        return "[REDACTED_SECRET]", 1
    if isinstance(value, dict):
        sanitized: dict = {}
        count = 0
        for item_key, item_value in value.items():
            sanitized_value, item_count = _sanitize_value(
                item_value, roots, str(item_key)
            )
            sanitized[item_key] = sanitized_value
            count += item_count
        return sanitized, count
    if isinstance(value, list):
        sanitized_list = []
        count = 0
        for item in value:
            sanitized_item, item_count = _sanitize_value(item, roots)
            sanitized_list.append(sanitized_item)
            count += item_count
        return sanitized_list, count
    if isinstance(value, str):
        return _redact_string(value, roots)
    return value, 0


def _sanitize_json_columns(connection: sqlite3.Connection, roots: tuple[str, ...]) -> int:
    redactions = 0
    tables = _table_names(connection)
    for table, columns in JSON_COLUMNS.items():
        if table not in tables:
            continue
        available = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        for column in columns:
            if column not in available:
                continue
            rows = connection.execute(
                f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
            ).fetchall()
            for rowid, raw in rows:
                try:
                    value = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as error:
                    raise ValueError(f"Invalid JSON in portable state: {table}.{column}") from error
                sanitized, count = _sanitize_value(value, roots)
                if count:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                        (json.dumps(sanitized, ensure_ascii=False, sort_keys=True), rowid),
                    )
                    redactions += count
    if "revisions" in tables:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("revisions")')
        }
        if "filename" in columns:
            for rowid, filename in connection.execute(
                "SELECT rowid,filename FROM revisions"
            ).fetchall():
                relative = _safe_project_relative(filename)
                portable = (
                    relative.as_posix()
                    if relative is not None
                    else filename.replace("\\", "/").split("/")[-1]
                )
                if portable != filename:
                    connection.execute(
                        "UPDATE revisions SET filename=? WHERE rowid=?", (portable, rowid)
                    )
                    redactions += 1
    return redactions


def _audit_bytes(data: bytes, *, label: str, roots: tuple[str, ...]) -> None:
    if SECRET_RE.search(data):
        raise ValueError(f"Bundle payload contains a credential-like secret: {label}")
    if PRIVATE_PATH_RE.search(data):
        raise ValueError(f"Bundle payload contains a private absolute path: {label}")
    for root in roots:
        for candidate in {root, root.replace("\\", "/"), root.replace("/", "\\")}:
            if candidate and candidate.encode("utf-8", errors="ignore") in data:
                raise ValueError(f"Bundle payload contains the local project path: {label}")


def _audit_file(path: Path, *, label: str, roots: tuple[str, ...]) -> None:
    data = path.read_bytes()
    _audit_bytes(data, label=label, roots=roots)
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    _audit_bytes(
                        archive.read(info), label=f"{label}!{info.filename}", roots=roots
                    )


def _portable_project_issues(project: ManuscriptProject) -> list[str]:
    verification = project.verify()
    issues = []
    if verification["database"] != "ok":
        issues.append(f"database integrity check: {verification['database']}")
    for revision in verification["revisions"]:
        issues.extend(
            f"revision {revision['revision_uid']}: {issue}" for issue in revision["issues"]
        )
    for row in project.connection.execute(
        """SELECT r.run_id FROM runs r
           WHERE EXISTS(SELECT 1 FROM run_units u WHERE u.run_id=r.run_id)
           ORDER BY r.run_id"""
    ):
        status = project.run_status(row[0])
        issues.extend(
            f"run {row[0]}: {issue['message']}" for issue in status["issues"]
        )
    return issues


def _portable_files(project: ManuscriptProject) -> list[tuple[str, Path]]:
    files: dict[str, Path] = {}
    reserved = {"manifest.json", "project.json", "editroast.sqlite"}
    for row in project.connection.execute(
        "SELECT revision_uid,snapshot_path FROM manuscript_revisions ORDER BY id"
    ):
        relative = _safe_project_relative(row["snapshot_path"])
        if relative is None:
            raise ValueError(f"Unsafe revision path: {row['snapshot_path']}")
        if relative.as_posix() in reserved:
            raise ValueError(f"Revision path conflicts with bundle metadata: {relative.as_posix()}")
        files[relative.as_posix()] = project.root / relative
    for row in project.connection.execute(
        "SELECT run_id,unit_id,result_path FROM run_units WHERE status='completed'"
    ):
        relative = _safe_project_relative(row["result_path"] or "")
        if relative is None:
            raise ValueError(f"Unsafe result path for {row['run_id']}:{row['unit_id']}")
        if relative.as_posix() in reserved:
            raise ValueError(f"Run result conflicts with bundle metadata: {relative.as_posix()}")
        files[relative.as_posix()] = project.root / relative
    return sorted(files.items())


def _write_zip(source_root: Path, destination: Path, entry_paths: list[str]) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in sorted(entry_paths):
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (source_root / Path(*PurePosixPath(relative).parts)).read_bytes())


def export_project_bundle(project: ManuscriptProject, destination: str | Path) -> dict:
    target = Path(destination).resolve()
    if target.suffix.lower() != ".editroast":
        raise ValueError("Portable project bundles must use the .editroast extension")
    if target.exists():
        raise ValueError(f"Bundle destination already exists: {target}")
    issues = _portable_project_issues(project)
    if issues:
        raise ValueError("Project is not safe to export: " + "; ".join(issues))
    target.parent.mkdir(parents=True, exist_ok=True)
    roots = (str(project.root), str(Path.home()))
    with tempfile.TemporaryDirectory(prefix="editroast-bundle-") as temporary:
        staging = Path(temporary)
        database_path = staging / "editroast.sqlite"
        backup = sqlite3.connect(database_path)
        try:
            project.connection.backup(backup)
            redactions = _sanitize_json_columns(backup, roots)
            backup.commit()
            backup.execute("VACUUM")
            backup.commit()
        finally:
            backup.close()

        project_row = project.connection.execute("SELECT * FROM projects LIMIT 1").fetchone()
        project_json = {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_row["id"],
            "created_at": project_row["created_at"],
            "title": project_row["title"],
            "author": project_row["author"],
            "language": project_row["language"],
        }
        (staging / "project.json").write_text(
            json.dumps(project_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        portable_files = _portable_files(project)
        for relative, source in portable_files:
            destination_path = staging / Path(*PurePosixPath(relative).parts)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination_path)

        with ManuscriptProject.open(staging) as staged_project:
            staged_issues = _portable_project_issues(staged_project)
            if staged_issues:
                raise ValueError(
                    "Portable copy failed integrity checks: " + "; ".join(staged_issues)
                )

        payload_paths = ["project.json", "editroast.sqlite"] + [
            relative for relative, _ in portable_files
        ]
        for relative in payload_paths:
            _audit_file(
                staging / Path(*PurePosixPath(relative).parts), label=relative, roots=roots
            )
        entries = [
            {
                "path": relative,
                "sha256": sha256_file(staging / Path(*PurePosixPath(relative).parts)),
                "size": (staging / Path(*PurePosixPath(relative).parts)).stat().st_size,
            }
            for relative in sorted(payload_paths)
        ]
        manifest = {
            "format": BUNDLE_FORMAT,
            "format_version": BUNDLE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "project_id": project_row["id"],
            "exported_at": utc_now(),
            "sanitization": {
                "redacted_values": redactions,
                "excluded": ["SQLite WAL/SHM", ".staging", "unreferenced work files"],
            },
            "revision_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT revision_uid FROM manuscript_revisions ORDER BY id"
                )
            ],
            "finding_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT finding_id FROM editorial_findings ORDER BY finding_id"
                )
            ],
            "decision_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT decision_id FROM author_decisions ORDER BY decision_id"
                )
            ],
            "application_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT application_uid FROM change_applications ORDER BY created_at,application_uid"
                )
            ],
            "run_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT run_id FROM runs ORDER BY created_at,run_id"
                )
            ],
            "editorial_review_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT review_id FROM editorial_board_reviews ORDER BY created_at,review_id"
                )
            ],
            "editorial_recheck_ids": [
                row[0] for row in project.connection.execute(
                    "SELECT recheck_id FROM editorial_board_rechecks ORDER BY created_at,recheck_id"
                )
            ],
            "entries": entries,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_bundle = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            _write_zip(staging, temporary_bundle, ["manifest.json", *payload_paths])
            os.replace(temporary_bundle, target)
        finally:
            temporary_bundle.unlink(missing_ok=True)
    return {**manifest, "bundle": str(target), "bundle_sha256": sha256_file(target)}


def _validate_member(info: zipfile.ZipInfo) -> None:
    pure = PurePosixPath(info.filename)
    mode = info.external_attr >> 16
    if (
        not info.filename
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in info.filename
        or (pure.parts and ":" in pure.parts[0])
    ):
        raise ValueError(f"Unsafe bundle path: {info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError("Encrypted bundle members are not supported")
    if (mode & 0o170000) == 0o120000:
        raise ValueError(f"Bundle symlink member is not allowed: {info.filename}")
    if info.file_size > MAX_BUNDLE_MEMBER_SIZE:
        raise ValueError(f"Bundle member is unreasonably large: {info.filename}")
    if info.file_size and info.compress_size == 0:
        raise ValueError(f"Bundle member has an invalid compressed size: {info.filename}")
    if info.compress_size and info.file_size / info.compress_size > MAX_BUNDLE_COMPRESSION_RATIO:
        raise ValueError(f"Bundle member compression ratio is unsafe: {info.filename}")


def _validated_bundle(path: str | Path) -> tuple[Path, dict]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError(f"Bundle not found: {source}")
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as error:
        raise ValueError(f"Invalid EditRoast bundle: {error}") from error
    with archive:
        members = archive.infolist()
        if len(members) > MAX_BUNDLE_MEMBERS:
            raise ValueError(f"Bundle has too many members ({len(members)})")
        if len({info.filename for info in members}) != len(members):
            raise ValueError("Bundle contains duplicate member names")
        total = 0
        for info in members:
            _validate_member(info)
            total += info.file_size
            if total > MAX_BUNDLE_UNCOMPRESSED:
                raise ValueError("Bundle uncompressed payload is too large")
        names = {info.filename for info in members if not info.is_dir()}
        if "manifest.json" not in names:
            raise ValueError("Bundle manifest is missing")
        if archive.getinfo("manifest.json").file_size > 2 * 1024 * 1024:
            raise ValueError("Bundle manifest is unreasonably large")
        try:
            manifest_bytes = archive.read("manifest.json")
            _audit_bytes(manifest_bytes, label="manifest.json", roots=())
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Bundle manifest is invalid JSON") from error
        if manifest.get("format") != BUNDLE_FORMAT:
            raise ValueError("Not an EditRoast project bundle")
        if manifest.get("format_version") != BUNDLE_VERSION:
            raise ValueError(
                f"Unsupported bundle format version: {manifest.get('format_version')}"
            )
        if not isinstance(manifest.get("schema_version"), int):
            raise ValueError("Bundle schema version is missing")
        if manifest["schema_version"] > SCHEMA_VERSION:
            raise ValueError(
                f"Bundle schema {manifest['schema_version']} is newer than supported schema {SCHEMA_VERSION}"
            )
        if not isinstance(manifest.get("project_id"), str) or not manifest["project_id"]:
            raise ValueError("Bundle project ID is missing")
        identifier_keys = [
            "revision_ids", "finding_ids", "decision_ids", "application_ids", "run_ids",
        ]
        if manifest["schema_version"] >= 7:
            identifier_keys.extend(("editorial_review_ids", "editorial_recheck_ids"))
        for identifier_key in identifier_keys:
            identifiers = manifest.get(identifier_key)
            if (
                not isinstance(identifiers, list)
                or not all(isinstance(item, str) and item for item in identifiers)
                or len(set(identifiers)) != len(identifiers)
            ):
                raise ValueError(f"Bundle {identifier_key} manifest is malformed")
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ValueError("Bundle entry manifest is missing")
        expected: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
                raise ValueError("Bundle entry manifest is malformed")
            if (
                not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str)
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise ValueError("Bundle entry manifest has invalid values")
            relative = _safe_project_relative(entry["path"])
            if relative is None or entry["path"] == "manifest.json":
                raise ValueError(f"Unsafe manifest entry path: {entry.get('path')}")
            if entry["path"] in expected:
                raise ValueError(f"Duplicate manifest entry: {entry['path']}")
            expected[entry["path"]] = entry
        if names != {"manifest.json", *expected}:
            raise ValueError("Bundle members do not match the manifest")
        if not {"project.json", "editroast.sqlite"} <= set(expected):
            raise ValueError("Bundle is missing required project state")
        for relative, entry in expected.items():
            info = archive.getinfo(relative)
            if info.file_size != entry["size"]:
                raise ValueError(f"Bundle size mismatch: {relative}")
            digest = hashlib.sha256()
            with archive.open(info) as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"Bundle SHA-256 mismatch: {relative}")
            data = archive.read(info)
            _audit_bytes(data, label=relative, roots=())
            if Path(relative).suffix.lower() == ".docx":
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as nested:
                        for nested_info in nested.infolist():
                            if not nested_info.is_dir():
                                _audit_bytes(
                                    nested.read(nested_info),
                                    label=f"{relative}!{nested_info.filename}",
                                    roots=(),
                                )
                except zipfile.BadZipFile as error:
                    raise ValueError(f"Invalid DOCX snapshot in bundle: {relative}") from error
    return source, manifest


def inspect_project_bundle(path: str | Path) -> dict:
    source, manifest = _validated_bundle(path)
    return {
        **manifest,
        "bundle": str(source),
        "bundle_sha256": sha256_file(source),
        "entry_count": len(manifest["entries"]),
        "total_size": sum(entry["size"] for entry in manifest["entries"]),
    }


def import_project_bundle(path: str | Path, destination: str | Path) -> dict:
    source, manifest = _validated_bundle(path)
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError(f"Import destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".editroast-import-", dir=target.parent))
    try:
        with zipfile.ZipFile(source) as archive:
            for entry in manifest["entries"]:
                relative = _safe_project_relative(entry["path"])
                if relative is None:
                    raise ValueError(f"Unsafe manifest entry path: {entry['path']}")
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry["path"]) as source_handle, output.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
        with ManuscriptProject.open(staging) as project:
            if project.status()["project"]["id"] != manifest["project_id"]:
                raise ValueError("Imported project ID does not match the bundle manifest")
            issues = _portable_project_issues(project)
            if issues:
                raise ValueError("Imported project failed integrity checks: " + "; ".join(issues))
            revision_ids = [item["revision_uid"] for item in project.revisions()]
            finding_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT finding_id FROM editorial_findings ORDER BY finding_id"
                )
            ]
            decision_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT decision_id FROM author_decisions ORDER BY decision_id"
                )
            ]
            application_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT application_uid FROM change_applications ORDER BY created_at,application_uid"
                )
            ]
            run_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT run_id FROM runs ORDER BY created_at,run_id"
                )
            ]
            editorial_review_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT review_id FROM editorial_board_reviews ORDER BY created_at,review_id"
                )
            ]
            editorial_recheck_ids = [
                row[0] for row in project.connection.execute(
                    "SELECT recheck_id FROM editorial_board_rechecks ORDER BY created_at,recheck_id"
                )
            ]
            if revision_ids != manifest["revision_ids"]:
                raise ValueError("Imported revision IDs do not match the bundle manifest")
            if finding_ids != manifest["finding_ids"]:
                raise ValueError("Imported finding IDs do not match the bundle manifest")
            if decision_ids != manifest["decision_ids"]:
                raise ValueError("Imported decision IDs do not match the bundle manifest")
            if application_ids != manifest["application_ids"]:
                raise ValueError("Imported application IDs do not match the bundle manifest")
            if run_ids != manifest["run_ids"]:
                raise ValueError("Imported run IDs do not match the bundle manifest")
            if editorial_review_ids != manifest.get("editorial_review_ids", []):
                raise ValueError("Imported editorial review IDs do not match the bundle manifest")
            if editorial_recheck_ids != manifest.get("editorial_recheck_ids", []):
                raise ValueError("Imported editorial re-check IDs do not match the bundle manifest")
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "project": str(target),
        "project_id": manifest["project_id"],
        "schema_version": manifest["schema_version"],
        "revision_ids": manifest["revision_ids"],
        "finding_ids": manifest["finding_ids"],
        "decision_ids": manifest["decision_ids"],
        "application_ids": manifest["application_ids"],
        "run_ids": manifest["run_ids"],
        "editorial_review_ids": manifest.get("editorial_review_ids", []),
        "editorial_recheck_ids": manifest.get("editorial_recheck_ids", []),
        "bundle_sha256": sha256_file(source),
    }
