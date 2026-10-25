#!/usr/bin/env python3
"""Review local Python runtime and release ZIP payloads for risky primitives."""
import ast
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULES = ("install.py", "scripts/manuscript_workflow.py", "scripts/build_release.py")
ALLOWED = {"__future__", "argparse", "dataclasses", "datetime", "json", "pathlib", "re", "shutil", "sys", "tempfile", "zipfile", "xml"}
BAD_CALLS = {"eval", "exec", "os.system", "os.popen", "subprocess.run", "subprocess.Popen", "socket.create_connection", "urllib.request.urlopen"}
BAD_PARTS = {".env", ".git", ".ssh", ".aws", "id_rsa", "node_modules", "__pycache__"}
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{25,}|ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY-----)")

def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return name(node.value) + "." + node.attr
    return ""

def main():
    errors = []
    for rel in MODULES:
        path = ROOT / rel
        src = path.read_text("utf-8")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                if module.split(".", 1)[0] not in ALLOWED:
                    errors.append(f"{rel}:{node.lineno} unexpected import: {module}")
            if isinstance(node, ast.Call) and name(node.func) in BAD_CALLS:
                errors.append(f"{rel}:{node.lineno} unexpected call: {name(node.func)}")
        if SECRET.search(src):
            errors.append(f"{rel}: potential embedded secret")
    archives = sorted((ROOT / "dist").glob("editroast-*-v*.zip"))
    if len(archives) != 3:
        errors.append(f"Expected exactly 3 current release ZIPs; found {len(archives)}")
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                parts = pathlib.PurePosixPath(info.filename).parts
                if any(part in BAD_PARTS for part in parts) or ".." in parts or info.filename.startswith("/"):
                    errors.append(f"{archive.name}: unsafe/private entry {info.filename}")
                if not info.is_dir() and info.filename.endswith((".py", ".md", ".yaml", ".txt", ".json")):
                    if SECRET.search(z.read(info.filename).decode("utf-8", errors="replace")):
                        errors.append(f"{archive.name}: suspicious secret in {info.filename}")
    for error in errors:
        print("FAIL", error, file=sys.stderr)
    print(f"Audited: {len(MODULES)} Python files and {len(archives)} built ZIPs")
    print("Scope excludes AI provider, agent tools/extensions and operating system.")
    if errors:
        return 1
    print("PASS: no unexpected imports, risky execution calls, private ZIP paths or credential patterns.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
