#!/usr/bin/env python3
"""Compatibility entry point for the canonical local project ledger."""

from __future__ import annotations

import sys
from pathlib import Path


def _load_core() -> None:
    script_dir = Path(__file__).resolve().parent
    candidates = (script_dir.parent / "src", script_dir)
    for candidate in candidates:
        if (candidate / "editroast_core").is_dir():
            sys.path.insert(0, str(candidate))
            return
    raise RuntimeError("Bundled editroast_core package is missing")


_load_core()

from editroast_core.editroast_state import *  # noqa: E402,F403
from editroast_core.editroast_state import main as _main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_main())
