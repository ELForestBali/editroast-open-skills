# Open Skills security review

This is a reproducible maintainer-operated source check, NOT an independent security certification.

## Local processing
The bundled Python scripts read manuscript files, prepare chunk workspaces, track progress, and validate quote references on the author's machine. Reviewed Python entry points do not use network libraries or outbound upload calls. The original manuscript is not sent to EditRoast by these scripts.

## How to reproduce
From the public repository root with Python 3.10 or later, execute the following commands in order:

    python scripts/build_release.py
    python scripts/security_check.py
    python -m unittest discover -s tests -v

GitHub Actions runs these checks for every pull request and relevant push. The static check verifies imports and risky execution primitives in the installer, wrappers, canonical Core, and build/security scripts. All five freshly built ZIP payloads (four standalone skills plus the complete pack) are checksum-verified and scanned for private paths, archive traversal, and common embedded secret signatures.

## Important limitations
Codex, Claude Code, or another AI provider may receive the manuscript passages sent for analysis; these providers have their own privacy and retention terms. Independent agent tools or extensions may perform network operations. This repository cannot certify their behavior. A static audit does not guarantee that a machine or account is free of malware and cannot guarantee model accuracy.

Use the open-source code and trusted GitHub release, inspect contents before executing, review your agent's permissions, and never commit your manuscript, workspace or credentials. This test supports the precise claim "bundled Python workflow makes no network requests", not "all AI processing is offline".

See `PRIVACY-SECURITY-LICENSES.md` for the complete trust and license boundary.

Last reviewed: October 28, 2026.
