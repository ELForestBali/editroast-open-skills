#!/usr/bin/env python3
"""Audit maintained Python and release archives for risky or private content."""

from __future__ import annotations

import ast
import hashlib
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ARCHIVES = 5
ALLOWED_IMPORTS = {
    "__future__",
    "argparse",
    "ast",
    "bisect",
    "csv",
    "cli",
    "dataclasses",
    "datetime",
    "difflib",
    "editroast_core",
    "hashlib",
    "io",
    "json",
    "manuscript_state",
    "os",
    "pathlib",
    "project_bundle",
    "re",
    "revision_diff",
    "safe_apply",
    "scripts",
    "shutil",
    "sqlite3",
    "stat",
    "sys",
    "tempfile",
    "uuid",
    "zipfile",
    "xml",
}
NETWORK_IMPORTS = {"http", "requests", "socket", "urllib", "ftplib", "smtplib"}
BAD_CALLS = {
    "eval",
    "exec",
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.Popen",
    "socket.create_connection",
    "urllib.request.urlopen",
}
BAD_PARTS = {
    ".env",
    ".git",
    ".ssh",
    ".aws",
    "authors",
    "legacy",
    "node_modules",
    "__pycache__",
}
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{25,}|ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,}|"
    r"-----BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY-----)"
)


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{call_name(node.value)}.{node.attr}"
    return ""


def maintained_modules() -> list[Path]:
    modules = [ROOT / "install.py"]
    modules.extend(sorted((ROOT / "scripts").glob("*.py")))
    modules.extend(sorted((ROOT / "src" / "editroast_core").glob("*.py")))
    return modules


def audit_python(path: Path) -> list[str]:
    relative = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative)
    errors: list[str] = []
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""]
        for module in imported:
            root = module.split(".", 1)[0]
            if root in NETWORK_IMPORTS:
                errors.append(f"{relative}:{node.lineno} network import: {module}")
            elif root not in ALLOWED_IMPORTS:
                errors.append(f"{relative}:{node.lineno} unexpected import: {module}")
        if isinstance(node, ast.Call) and call_name(node.func) in BAD_CALLS:
            errors.append(f"{relative}:{node.lineno} unexpected call: {call_name(node.func)}")
    if SECRET.search(source):
        errors.append(f"{relative}: potential embedded secret")
    return errors


def read_manifest(dist: Path) -> tuple[list[Path], list[str]]:
    manifest = dist / "SHA256SUMS.txt"
    if not manifest.is_file():
        return [], ["dist/SHA256SUMS.txt is missing; run the release builder first"]
    archives: list[Path] = []
    errors: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            expected, filename = line.split("  ", 1)
        except ValueError:
            errors.append(f"Malformed checksum line: {line}")
            continue
        archive = dist / filename
        archives.append(archive)
        if not archive.is_file():
            errors.append(f"Missing archive from checksum manifest: {filename}")
            continue
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"Checksum mismatch: {filename}")
    if len(archives) != EXPECTED_ARCHIVES:
        errors.append(
            f"Expected {EXPECTED_ARCHIVES} current release ZIPs in manifest; found {len(archives)}"
        )
    return archives, errors


def audit_archive(path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            pure = PurePosixPath(name)
            lowered = {part.lower() for part in pure.parts}
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or "\\" in name
                or (pure.parts and ":" in pure.parts[0])
                or lowered & BAD_PARTS
            ):
                errors.append(f"{path.name}: unsafe/private entry {name}")
            if not info.is_dir() and name.endswith((".py", ".md", ".yaml", ".txt", ".json")):
                text = archive.read(info).decode("utf-8", errors="replace")
                if SECRET.search(text):
                    errors.append(f"{path.name}: suspicious secret in {name}")
    return errors


def main() -> int:
    modules = maintained_modules()
    errors = [error for module in modules for error in audit_python(module)]
    dist = ROOT / "dist"
    archives, manifest_errors = read_manifest(dist)
    errors.extend(manifest_errors)
    all_archives = sorted(dist.glob("*.zip"))
    for archive in all_archives:
        if archive.is_file():
            errors.extend(audit_archive(archive))

    for error in errors:
        print("FAIL", error, file=sys.stderr)
    print(
        f"Audited: {len(modules)} Python files, {len(archives)} manifested ZIPs, "
        f"and {len(all_archives)} total ZIP payloads"
    )
    print("Scope excludes AI providers, agent tools/extensions, and the operating system.")
    if errors:
        return 1
    print("PASS: no network imports, risky execution calls, private paths, or credential patterns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
