#!/usr/bin/env python3
"""Repository and bundled entry point for the durable EditRoast Core CLI."""

from __future__ import annotations

import sys
from pathlib import Path


script_dir = Path(__file__).resolve().parent
for candidate in (script_dir.parent / "src", script_dir):
    if (candidate / "editroast_core").is_dir():
        sys.path.insert(0, str(candidate))
        break
else:
    raise RuntimeError("Bundled editroast_core package is missing")

from editroast_core.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
