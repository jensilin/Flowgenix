"""Vercel entry point.

Vercel serves one Python function per file under `api/`, and `vercel.json`
rewrites every request here, so this module hands the whole site — page, static
assets and migration endpoints — to the same FastAPI app used locally.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui.app import app  # noqa: E402

__all__ = ["app"]
