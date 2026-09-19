"""Static checks that the migration UI is wired to the backend it talks to."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "ui" / "static"
APP_JS = STATIC / "app.js"
INDEX_HTML = STATIC / "index.html"
STYLES_CSS = STATIC / "styles.css"
APP_PY = REPO_ROOT / "ui" / "app.py"


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def server() -> str:
    return APP_PY.read_text(encoding="utf-8")


def test_every_element_id_used_by_js_exists_in_the_html(js, html):
    referenced = set(re.findall(r"getElementById\(\"([^\"]+)\"\)", js))
    declared = set(re.findall(r"id=\"([^\"]+)\"", html))
    missing = sorted(referenced - declared)
    assert not missing, f"app.js looks up ids that index.html does not define: {missing}"


def test_migration_elements_are_present(html):
    for element_id in (
        "migration-file",
        "migration-source-version",
        "migration-target-version",
        "output-json",
        "output-xml",
        "migration-analyze",
        "migration-generate",
        "migration-report",
        "migration-stats",
        "migration-details",
        "migration-artifacts",
        "theme-toggle",
        "log",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_page_is_migration_only(html):
    assert "Flowgenix" in html
    assert "Create flow from prompt" not in html
    assert "Prompt assistant" not in html
    assert "JSON → CSV" not in html
    assert "Cursor API key" not in html


def test_every_migration_endpoint_the_js_calls_is_defined_on_the_server(js, server):
    called = set(re.findall(r"[\"'](/api/migration/[a-z-]+)[\"']", js))
    assert called, "no migration endpoints referenced from app.js"
    for path in called:
        assert f'"{path}"' in server, f"app.js calls {path} but ui/app.py does not define it"


def test_migration_css_classes_used_by_js_are_styled(js, css):
    used = set(re.findall(r"class=\"(migration-[a-z-]+)", js))
    used |= set(re.findall(r"\"(outcome-tag)\"", js))
    for name in used:
        assert f".{name}" in css, f"class .{name} is emitted by app.js but never styled"


def test_downloads_are_built_in_the_browser_not_fetched_from_the_server(js, server):
    """The app has to survive a host with no disk between two requests."""
    assert "URL.createObjectURL" in js.split("function artifactAnchor(", 1)[1]
    assert "downloadUrl" not in js
    assert "/api/migration/download" not in server
    assert "MIGRATIONS_DIR" not in server


def test_generated_artifacts_carry_their_own_contents(server):
    builder = server.split("def _build_migration_artifacts(", 1)[1].split("\ndef ", 1)[0]
    for key in ("migratedFlow", "migratedXml", "reportMarkdown", "report"):
        assert f'"{key}"' in builder, f"the generate response no longer offers {key}"


def test_upload_uses_text_in_json_transport(js):
    reader = js.split("async function readMigrationUpload(", 1)[1].split("\nasync function ", 1)[0]
    assert "await file.text()" in reader
    assert "source_text" in reader
    assert "FormData" not in reader
