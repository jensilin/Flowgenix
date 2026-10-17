"""Component changes are suggestions: nothing is applied unless its id is accepted.

Format changes (bundle versions, XML → JSON, the target's JSON dialect) are not
suggestions and always happen.
"""

from __future__ import annotations

import json

from fidelity import compare_flows
from flow_document import parse_flow_file
from migration_engine import analyze_migration, generate_migrated_flow

_STD = "org.apache.nifi.processors.standard."

_HTTP_1X = json.dumps(
    {
        "flowContents": {
            "identifier": "root",
            "name": "http",
            "processors": [
                {
                    "identifier": "fetch",
                    "name": "Fetch",
                    "type": _STD + "GetHTTP",
                    "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.2.0"},
                    "properties": {"URL": "https://example.com/feed", "Filename": "feed.json", "Data Timeout": "30 sec"},
                    "schedulingStrategy": "TIMER_DRIVEN",
                    "autoTerminatedRelationships": [],
                },
                {
                    "identifier": "push",
                    "name": "Push",
                    "type": _STD + "PostHTTP",
                    "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.2.0"},
                    "properties": {"URL": "https://partner.example.com", "Send as FlowFile": "false"},
                    "schedulingStrategy": "TIMER_DRIVEN",
                    "autoTerminatedRelationships": ["success"],
                },
                {
                    "identifier": "log",
                    "name": "Log",
                    "type": _STD + "LogAttribute",
                    "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.2.0"},
                    "properties": {},
                    "schedulingStrategy": "TIMER_DRIVEN",
                },
            ],
            "connections": [
                {
                    "identifier": "c1",
                    "name": "",
                    "source": {"id": "fetch", "groupId": "root", "type": "PROCESSOR", "name": "Fetch"},
                    "destination": {"id": "push", "groupId": "root", "type": "PROCESSOR", "name": "Push"},
                    "selectedRelationships": ["success"],
                },
                {
                    "identifier": "c2",
                    "name": "",
                    "source": {"id": "push", "groupId": "root", "type": "PROCESSOR", "name": "Push"},
                    "destination": {"id": "log", "groupId": "root", "type": "PROCESSOR", "name": "Log"},
                    "selectedRelationships": ["failure"],
                },
            ],
        }
    }
)

_MODERN_2X = json.dumps(
    {
        "flowContents": {
            "identifier": "root",
            "name": "modern",
            "processors": [
                {
                    "identifier": "call",
                    "name": "Call",
                    "type": _STD + "InvokeHTTP",
                    "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "2.11.0"},
                    "properties": {"HTTP URL": "https://example.com/api", "HTTP Method": "GET"},
                    "schedulingStrategy": "TIMER_DRIVEN",
                },
                {
                    "identifier": "hash",
                    "name": "Hash",
                    "type": _STD + "CryptographicHashContent",
                    "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "2.11.0"},
                    "properties": {"Hash Algorithm": "SHA-256", "fail_when_empty": "false"},
                    "schedulingStrategy": "TIMER_DRIVEN",
                },
            ],
        }
    }
)


def _run(text: str, source: str, target: str, accept=lambda s: False, output_format="json_snapshot"):
    doc = parse_flow_file(text, "f.json")
    analysis = analyze_migration(doc, source, target)
    accepted = [s["id"] for s in analysis.suggestions() if accept(s)]
    result = generate_migrated_flow(doc, analysis, output_format=output_format, accepted=accepted)
    procs = {p["name"]: p for p in result.migrated["flowContents"]["processors"]}
    return doc, analysis, result, procs


def _accept_all(_s) -> bool:
    return True


# --- What is offered ----------------------------------------------------------


def test_each_suggestion_has_a_stable_id_and_direction():
    _, analysis, _, _ = _run(_HTTP_1X, "1.2.0", "2.11.0")
    suggestions = analysis.suggestions()
    assert {s["component"] for s in suggestions} == {"Fetch", "Push"}
    for s in suggestions:
        assert s["id"] == f"{s['componentId']}:{s['kind']}:{s['from'].rsplit('.', 1)[-1]}"
        assert s["direction"] == "upgrade"
        assert s["required"] is True
    summary = analysis.summary()
    assert summary["suggestionCount"] == summary["requiredSuggestionCount"] == 2
    assert analysis.to_dict()["suggestions"] == suggestions


def test_deprecated_but_available_is_offered_as_optional():
    _, analysis, _, _ = _run(_HTTP_1X, "1.2.0", "1.16.3")
    assert [s["required"] for s in analysis.suggestions()] == [False, False]


def test_nothing_is_offered_before_the_deprecation():
    _, analysis, _, _ = _run(_HTTP_1X, "1.2.0", "1.2.0")
    assert analysis.suggestions() == []


# --- Upgrade: none, all, partial ----------------------------------------------


def test_nothing_changes_without_acceptance():
    _, _, result, procs = _run(_HTTP_1X, "1.2.0", "2.11.0")
    assert procs["Fetch"]["type"].endswith("GetHTTP")
    assert procs["Fetch"]["properties"]["URL"] == "https://example.com/feed"
    assert "MANUAL REVIEW REQUIRED" in procs["Fetch"]["comments"]
    assert not [c for c in result.applied_changes if c["change"] != "bundleVersion"]
    assert result.accepted == []
    assert len(result.declined) == 2


def test_declining_a_required_change_warns():
    _, _, result, _ = _run(_HTTP_1X, "1.2.0", "2.11.0")
    warning = next(w for w in result.warnings if "not accepted" in w)
    assert "2 required change(s)" in warning
    assert "GetHTTP → InvokeHTTP" in warning


def test_declining_an_optional_change_does_not_warn():
    _, _, result, _ = _run(_HTTP_1X, "1.2.0", "1.16.3")
    assert result.declined and not result.warnings


def test_accepting_a_swap_replaces_the_type_and_carries_configuration():
    _, _, result, procs = _run(_HTTP_1X, "1.2.0", "1.28.1", _accept_all)
    fetch = procs["Fetch"]
    assert fetch["type"] == _STD + "InvokeHTTP"
    assert fetch["properties"]["Remote URL"] == "https://example.com/feed"
    assert fetch["properties"]["Read Timeout"] == "30 sec"
    assert fetch["properties"]["HTTP Method"] == "GET"
    assert "Filename" not in fetch["properties"]
    assert "Replaced GetHTTP with InvokeHTTP" in fetch["comments"]
    assert "MANUAL REVIEW REQUIRED" not in fetch.get("comments", "")
    assert not result.skipped and not result.warnings


def test_a_swap_on_a_2x_target_uses_2x_property_names():
    _, _, _, procs = _run(_HTTP_1X, "1.2.0", "2.11.0", _accept_all)
    props = procs["Fetch"]["properties"]
    assert props["HTTP URL"] == "https://example.com/feed"
    assert props["Socket Read Timeout"] == "30 sec"
    assert "Remote URL" not in props


def test_a_swap_renames_relationships_on_connections_and_auto_termination():
    _, _, result, procs = _run(_HTTP_1X, "1.2.0", "2.11.0", _accept_all)
    conns = {c["identifier"]: c for c in result.migrated["flowContents"]["connections"]}
    assert conns["c1"]["selectedRelationships"] == ["Response"]
    assert conns["c2"]["selectedRelationships"] == ["Failure"]
    assert procs["Push"]["autoTerminatedRelationships"] == ["Original"]
    assert sum(1 for c in result.applied_changes if c["change"] == "relationship") == 2


def test_only_the_accepted_component_changes():
    _, _, result, procs = _run(_HTTP_1X, "1.2.0", "2.11.0", lambda s: s["component"] == "Fetch")
    assert procs["Fetch"]["type"].endswith("InvokeHTTP")
    assert procs["Push"]["type"].endswith("PostHTTP")
    assert "MANUAL REVIEW REQUIRED" in procs["Push"]["comments"]
    conns = {c["identifier"]: c for c in result.migrated["flowContents"]["connections"]}
    assert conns["c2"]["selectedRelationships"] == ["failure"]
    assert [d["component"] for d in result.declined] == ["Push"]


def test_bundle_versions_follow_the_target_whatever_is_accepted():
    _, _, _, procs = _run(_HTTP_1X, "1.2.0", "1.28.1")
    assert procs["Log"]["bundle"]["version"] == "1.28.1"


def test_accepted_swap_passes_the_fidelity_check():
    doc, _, result, _ = _run(_HTTP_1X, "1.2.0", "2.11.0", _accept_all)
    report = compare_flows(doc, result.migrated, result.applied_changes)
    assert [c["id"] for c in report["checks"] if not c["passed"]] == []


def test_unknown_accepted_ids_are_reported_and_ignored():
    doc = parse_flow_file(_HTTP_1X, "f.json")
    analysis = analyze_migration(doc, "1.2.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis, accepted=["nope:type:X"])
    assert result.unknown_accepted == ["nope:type:X"]
    assert result.accepted == []


# --- Accepting property and scheduling suggestions ------------------------------


def test_rename_and_scheduling_are_separate_choices(xml_template_1x):
    def only_rename(s) -> bool:
        return s["component"] == "Reshape Records" and s["kind"] == "property"

    _, _, result, procs = _run(xml_template_1x, "1.25.0", "2.11.0", only_rename)
    reshape = procs["Reshape Records"]
    assert "Jolt Transform" in reshape["properties"]
    assert reshape["schedulingStrategy"] == "EVENT_DRIVEN"
    assert any(d["kind"] == "schedulingStrategy" for d in result.declined)


def test_xml_source_is_still_converted_with_nothing_accepted(xml_template_1x):
    _, _, result, procs = _run(xml_template_1x, "1.25.0", "2.11.0")
    assert result.output_format == "json_snapshot"
    assert "Jolt Transformation DSL" in procs["Reshape Records"]["properties"]
    assert procs["Reshape Records"]["bundle"]["version"] == "2.11.0"


# --- Downgrade: none, all ----------------------------------------------------


def test_downgrade_offers_old_names_and_the_older_equivalent():
    _, analysis, _, _ = _run(_MODERN_2X, "2.11.0", "1.2.0")
    offered = {(s["component"], s["kind"], s["to"].rsplit(".", 1)[-1]) for s in analysis.suggestions()}
    assert offered == {("Call", "property", "Remote URL"), ("Hash", "type", "HashContent")}
    assert {s["direction"] for s in analysis.suggestions()} == {"downgrade"}


def test_downgrade_with_nothing_accepted_keeps_2x_configuration():
    _, _, result, procs = _run(_MODERN_2X, "2.11.0", "1.28.1")
    assert "HTTP URL" in procs["Call"]["properties"]
    assert procs["Call"]["bundle"]["version"] == "1.28.1"
    assert any("not accepted" in w for w in result.warnings)


def test_downgrade_with_everything_accepted():
    _, _, result, procs = _run(_MODERN_2X, "2.11.0", "1.2.0", _accept_all, "xml_template")
    assert procs["Call"]["properties"]["Remote URL"] == "https://example.com/api"
    assert procs["Hash"]["type"] == _STD + "HashContent"
    assert procs["Hash"]["properties"] == {"Hash Algorithm": "SHA-256"}
    assert 'encoding-version="1.1"' in result.xml_text
    assert "<version>1.2.0</version>" in result.xml_text


def test_downgrade_within_2x_offers_nothing():
    _, analysis, _, _ = _run(_MODERN_2X, "2.11.0", "2.6.0")
    assert analysis.suggestions() == []
    assert analysis.summary()["direction"] == "downgrade"
