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


def test_the_page_is_usable_on_a_phone(html, css, js):
    assert 'name="viewport"' in html and "width=device-width" in html
    assert "@media (max-width: 640px)" in css, "no phone breakpoint"

    narrow = css.split("@media (max-width: 640px)", 1)[1]
    # Under 16px, phone browsers zoom the whole page in when a control is focused.
    assert "font-size: 16px" in narrow, "form controls will trigger zoom-on-focus"
    # The findings table is four columns wide and has to restack.
    assert ".migration-table thead" in narrow
    for column in ("Component", "Type", "Outcome", "Notes"):
        assert f'data-label="{column}"' in js, f"stacked rows would lose the {column} heading"


def test_every_results_tab_has_its_panel(html):
    tabs = re.findall(r'role="tab"[^>]*aria-controls="([^"]+)"', html)
    assert tabs == [
        "panel-summary",
        "panel-changes",
        "panel-preview",
        "panel-fidelity",
        "panel-checklist",
        "panel-log",
    ]
    for panel in tabs:
        assert f'id="{panel}"' in html


def test_the_js_consumes_every_event_the_server_streams(js, server):
    for event in ("status", "error", "analysis", "preview", "generated"):
        assert f'"type": "{event}"' in server, f"ui/app.py never emits {event}"
        assert f'event.type === "{event}"' in js, f"app.js ignores {event} events"
    for key in ("readiness", "fidelity"):
        assert f'"{key}":' in server and f"event.{key}" in js


def test_sample_flow_and_paste_loading(js, html):
    assert 'SAMPLE_URL = "/static/samples/legacy-intake.xml"' in js
    assert (STATIC / "samples" / "legacy-intake.xml").is_file()
    assert "js-load-sample" in html
    assert 'document.addEventListener("paste"' in js


def test_hosted_size_limit_is_checked_before_upload(js):
    reader = js.split("async function readMigrationUpload(", 1)[1].split("\nasync function ", 1)[0]
    assert reader.index("sizeProblem(file.size)") < reader.index("await file.text()")
    assert "4.3 * 1024 * 1024" in js


def test_print_report_covers_summary_changes_and_checklist(html, css):
    printed = re.findall(r'id="(panel-[a-z]+)"[^>]*data-print="yes"', html)
    assert printed == ["panel-summary", "panel-changes", "panel-checklist"]
    assert "@media print" in css
    assert "window.print()" in (APP_JS.read_text(encoding="utf-8"))


def test_checklist_ticks_persist_per_flow_and_target(js):
    key = js.split("function checklistKey(", 1)[1].split("\n}", 1)[0]
    for part in ("u.filename", "u.size", "targetEl.value"):
        assert part in key
    assert "localStorage.setItem(checklistKey()" in js


def test_suggestions_are_opt_in_and_sent_with_generate(js, html, css, server):
    for element_id in (
        "suggestions",
        "suggestions-list",
        "suggestions-progress",
        "suggestions-accept-all",
        "suggestions-clear",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"
    assert html.index('id="suggestions"') < html.index('id="changes-filters"')
    assert "accepted_suggestions: list[str]" in server
    assert "body.accepted_suggestions = [...acceptedIds]" in js
    # Nothing starts ticked: the set is only filled by the user.
    assert "const acceptedIds = new Set();" in js
    reader = js.split("async function readMigrationUpload(", 1)[1].split("\nasync function ", 1)[0]
    assert "acceptedIds.clear()" in reader
    for name in ("suggestion-row", "suggestions-head", "suggestion-reason"):
        assert f".{name}" in css, f".{name} is emitted by app.js but never styled"


def test_changing_a_tick_discards_the_generated_file(js):
    changed = js.split("function suggestionChoicesChanged(", 1)[1].split("\n}", 1)[0]
    assert "invalidateGeneration()" in changed
    invalidate = js.split("function invalidateGeneration(", 1)[1].split("\n}", 1)[0]
    for part in ("URL.revokeObjectURL", "state.generation = null", "syncGenerateButton()"):
        assert part in invalidate


def test_preview_can_show_the_flow_before_and_after_the_ticked_changes(js, html, css):
    select = html.split('id="preview-mode"', 1)[1].split("</select>", 1)[0]
    assert 'value="before"' in select and 'value="after"' in select
    toolbar = html.split('class="preview-toolbar"', 1)[1].split('id="preview-stage"', 1)[0]
    assert toolbar.index('id="preview-mode"') < toolbar.index('id="preview-crumbs"')
    for element_id in ("preview-note", "preview-legend"):
        assert f'id="{element_id}"' in html
    # Ticking or unticking redraws the "after" view straight away.
    changed = js.split("function suggestionChoicesChanged(", 1)[1].split("\n}", 1)[0]
    assert "refreshPreview()" in changed
    draw = js.split("function drawGroup(", 1)[1].split("\nfunction ", 1)[0]
    for part in ("processorLook(", "relationshipMaps()", "groupTone("):
        assert part in draw
    assert "details?.relationshipMap" in js
    for name in ("pv-add", "pv-conn-chg", "lg-add", "preview-mode", "pv-linked"):
        assert f".{name}" in css, f".{name} is used by the preview but never styled"


def test_preview_and_changes_link_to_each_other(js):
    assert "showInChanges(linked.dataset.component)" in js
    assert 'class="small show-in-preview"' in js
    assert "showInPreview(button.dataset.component)" in js


def test_rerunning_keeps_the_preview_place_but_a_new_file_resets_it(js):
    run = js.split("async function runMigration(", 1)[1].split("\nasync function ", 1)[0]
    assert "resetResults({ rerun: true })" in run
    reader = js.split("async function readMigrationUpload(", 1)[1].split("\nasync function ", 1)[0]
    assert "resetResults()" in reader
    assert "resetPreview({ keepPlace: rerun })" in js
    reset = js.split("function resetPreview(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (!keepPlace)" in reset and 'preview.mode = "after"' in reset


def test_switching_tabs_reveals_the_panel_under_the_sticky_tabs(js):
    select = js.split("function selectTab(", 1)[1].split("\nfunction ", 1)[0]
    assert "revealPanelTop(shown)" in select
    assert "function revealPanelTop(" in js


def test_index_links_assets_by_content_hash():
    from ui.app import _versioned_index

    page = _versioned_index()
    assert re.search(r'href="/static/styles\.css\?v=[0-9a-f]{12}"', page)
    assert re.search(r'src="/static/app\.js\?v=[0-9a-f]{12}"', page)


def test_upload_uses_text_in_json_transport(js):
    reader = js.split("async function readMigrationUpload(", 1)[1].split("\nasync function ", 1)[0]
    assert "await file.text()" in reader
    assert "source_text" in reader
    assert "FormData" not in reader
