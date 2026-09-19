"""Checks on the Vercel deployment contract.

A broken entry point or rewrite does not fail any other test — it fails the
deploy, silently, after the change is already merged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "api" / "index.py"
CONFIG = REPO_ROOT / "vercel.json"


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_the_entry_point_exposes_the_same_app_the_browser_talks_to():
    assert ENTRY.is_file(), "Vercel serves api/index.py; it is missing"
    source = ENTRY.read_text(encoding="utf-8")
    assert "from ui.app import app" in source
    assert "sys.path" in source, "the repo root must be importable from inside api/"


def test_every_request_is_routed_to_the_entry_point(config):
    rewrites = config.get("rewrites") or []
    assert any(
        r.get("source") == "/(.*)" and r.get("destination") == "/api/index" for r in rewrites
    ), f"vercel.json does not send all traffic to api/index: {rewrites}"


def test_static_assets_are_bundled_with_the_function(config):
    function = (config.get("functions") or {}).get("api/index.py") or {}
    assert "ui/static" in (function.get("includeFiles") or ""), (
        "the page, stylesheet, script and favicon live in ui/static and must be "
        "included, or the deployed site serves the API with no UI"
    )
    assert function.get("maxDuration", 0) >= 60, "large flows need more than the default budget"


def test_the_runtime_requirements_do_not_drag_in_the_test_tooling():
    runtime = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "fastapi" in runtime
    assert "pytest" not in runtime, "pytest belongs in requirements-dev.txt, not the bundle"


def test_nothing_is_written_unless_an_archive_directory_is_configured(tmp_path, monkeypatch):
    from ui import app as web

    artifacts = {
        "migratedFlow": {"name": "custom-madsor.json", "text": "{}"},
        "reportMarkdown": {"name": "custom-madsor-report.md", "text": "# report"},
    }

    monkeypatch.setattr(web, "ARCHIVE_DIR", "")
    web._archive_migration("custom-madsor", artifacts, {"ok": True})

    monkeypatch.setattr(web, "ARCHIVE_DIR", str(tmp_path / "keep"))
    web._archive_migration("custom-madsor", artifacts, {"ok": True})

    written = sorted(p.name.split("-", 2)[2] for p in (tmp_path / "keep").iterdir())
    assert written == ["custom-madsor-report.json", "custom-madsor-report.md", "custom-madsor.json"]


def test_the_deployment_leaves_out_local_scratch():
    ignored = (REPO_ROOT / ".vercelignore").read_text(encoding="utf-8").split()
    for entry in ("migrations/", "tests/", ".venv"):
        assert entry in ignored, f"{entry} should not be uploaded"
