"""Static checks that the migration UI is wired to the backend it talks to.

There is no JS test runner in this project, and the failure mode these guard
against is silent: `document.getElementById` returns null for a typo'd id, and
the first property access on it throws at load time, breaking the whole page
including the pre-existing features. A cheap static cross-check catches that.
"""

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
        "migration-toggle",
        "migration-body",
        "migration-file",
        "migration-source-version",
        "migration-target-version",
        "migration-model",
        "migration-use-ai",
        "migration-analyze",
        "migration-generate",
        "migration-report",
        "migration-stats",
        "migration-details",
        "migration-artifacts",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_migration_section_follows_the_existing_toggle_pattern(html):
    section = html.split('id="section-migration"', 1)[1].split("</div>\n      </div>", 1)[0]
    assert 'class="switch"' in section
    assert 'class="switch-track"' in section
    assert 'class="section-body hidden"' in section


def test_every_migration_endpoint_the_js_calls_is_defined_on_the_server(js, server):
    called = {
        path
        for path in re.findall(r"[\"'](/api/migration/[a-z-]+)[\"']", js)
    }
    # The generate/analyze endpoints are chosen dynamically, so pick those up too.
    called |= set(re.findall(r"[\"'](/api/migration/[a-z-]+)[\"']", js))
    assert called, "no migration endpoints referenced from app.js"
    for path in called:
        assert f'"{path}"' in server, f"app.js calls {path} but ui/app.py does not define it"


def test_migration_css_classes_used_by_js_are_styled(js, css):
    migration_block = js.split("--- NiFi migration ---", 1)[1]
    used = set(re.findall(r"class=\"(migration-[a-z-]+)", migration_block))
    used |= set(re.findall(r"\"(outcome-tag)\"", migration_block))
    for name in used:
        assert f".{name}" in css, f"class .{name} is emitted by app.js but never styled"


def test_migration_js_does_not_reference_undefined_css_variables(css):
    """--primary/--fg are referenced elsewhere in this file but never declared;
    the migration styles must not add to that."""
    migration_css = css.split("/* ---------- NiFi migration ---------- */", 1)[1]
    declared = set(re.findall(r"(--[a-z0-9-]+):", css))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", migration_css))
    undefined = sorted(used - declared)
    assert not undefined, f"migration CSS uses undeclared variables: {undefined}"


def test_model_dropdown_is_populated_from_the_shared_loader(js):
    """The migration model picker must reuse /api/models, not its own fetch."""
    loader = js.split("async function loadModels()", 1)[1].split("async function", 1)[0]
    assert "migrationModelEl" in loader


def test_upload_uses_the_projects_existing_text_in_json_transport(js):
    """Consistent with the JSON→CSV upload: file.text() into a JSON body."""
    reader = js.split("async function readMigrationUpload()", 1)[1].split("\nfunction ", 1)[0]
    assert "await file.text()" in reader
    assert "source_text" in reader
    assert "FormData" not in reader
