"""Shared test setup: make `debt_scan` importable as a module."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import debt_scan  # noqa: E402  (re-exported for convenience)

REPO_ROOT = _REPO_ROOT
SCRIPTS = _SCRIPTS

__all__ = ["debt_scan", "REPO_ROOT", "SCRIPTS"]
