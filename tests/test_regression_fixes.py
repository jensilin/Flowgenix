"""Fixes found by the every-version regression matrix (flowgenix_testing/matrix.py).

  * third-party NARs keep their own version; only Apache's follow the release
  * version detection ignores third-party NAR versions
  * the XML template writer keeps every field the reader understands, including
    remote process groups
  * the same template always produces the same output
  * migrating an already-migrated file does not stack Flowgenix's notes
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

from flow_document import parse_flow_file
from flow_graph import source_snapshot
from migration_engine import analyze_migration, generate_migrated_flow, migrated_json_text

ROOT = "a0000000-0000-4000-8000-000000000000"
APACHE = {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "1.19.1"}
CUSTOM = {"group": "com.example", "artifact": "example-nar", "version": "3.4.1"}


def _flow(**root_extra) -> dict:
    root = {
        "identifier": ROOT,
        "name": "Edge",
        "comments": "",
        "position": {"x": 0.0, "y": 0.0},
        "processors": [
            {
                "identifier": "a0000000-0000-4000-8000-000000000001",
                "name": "Generate",
                "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                "bundle": dict(APACHE),
                "position": {"x": 0.0, "y": 0.0},
                "schedulingStrategy": "TIMER_DRIVEN",
                "properties": {},
                "autoTerminatedRelationships": [],
            },
            {
                "identifier": "a0000000-0000-4000-8000-000000000002",
                "name": "Custom",
                "type": "com.example.Enrich",
                "bundle": dict(CUSTOM),
                "position": {"x": 0.0, "y": 240.0},
                "schedulingStrategy": "TIMER_DRIVEN",
                "concurrentlySchedulableTaskCount": 4,
                "runDurationMillis": 25,
                "retryCount": 3,
                "backoffMechanism": "YIELD_PROCESSOR",
                "maxBackoffPeriod": "5 mins",
                "style": {"background-color": "#f0e68c"},
                "properties": {"Secret": "x"},
                "propertyDescriptors": {
                    "Secret": {"name": "Secret", "displayName": "Secret", "sensitive": True, "dynamic": True}
                },
                "autoTerminatedRelationships": ["failure"],
            },
        ],
        "controllerServices": [
            {
                "identifier": "a0000000-0000-4000-8000-0000000000a1",
                "name": "Lookup",
                "type": "com.example.Lookup",
                "bundle": dict(CUSTOM),
                "annotationData": "<cfg/>",
                "properties": {},
            }
        ],
        "connections": [
            {
                "identifier": "a0000000-0000-4000-8000-0000000000c1",
                "name": "enriched",
                "selectedRelationships": ["success"],
                "source": {"id": "a0000000-0000-4000-8000-000000000001", "type": "PROCESSOR", "groupId": ROOT},
                "destination": {"id": "a0000000-0000-4000-8000-000000000002", "type": "PROCESSOR", "groupId": ROOT},
                "bends": [{"x": 200.0, "y": 360.0}],
                "prioritizers": ["org.apache.nifi.prioritizer.FirstInFirstOutPrioritizer"],
                "loadBalanceStrategy": "ROUND_ROBIN",
                "loadBalanceCompression": "COMPRESS_ATTRIBUTES_ONLY",
                "labelIndex": 0,
                "zIndex": 2,
            },
            {
                "identifier": "a0000000-0000-4000-8000-0000000000c2",
                "name": "",
                "selectedRelationships": ["success"],
                "source": {"id": "a0000000-0000-4000-8000-000000000002", "type": "PROCESSOR", "groupId": ROOT},
                "destination": {
                    "id": "a0000000-0000-4000-8000-0000000000d2",
                    "type": "REMOTE_INPUT_PORT",
                    "groupId": "a0000000-0000-4000-8000-0000000000d1",
                },
            },
        ],
        "inputPorts": [
            {
                "identifier": "a0000000-0000-4000-8000-0000000000e1",
                "name": "In",
                "comments": "Entry point",
                "type": "INPUT_PORT",
                "position": {"x": 480.0, "y": 0.0},
                "concurrentlySchedulableTaskCount": 2,
            }
        ],
        "outputPorts": [],
        "funnels": [],
        "labels": [
            {
                "identifier": "a0000000-0000-4000-8000-0000000000b1",
                "label": "Read me",
                "position": {"x": 900.0, "y": 0.0},
                "width": 240.0,
                "height": 80.0,
                "style": {"font-size": "14px"},
            }
        ],
        "remoteProcessGroups": [
            {
                "identifier": "a0000000-0000-4000-8000-0000000000d1",
                "name": "Remote Site",
                "position": {"x": 900.0, "y": 240.0},
                "targetUris": "https://remote.example:8443/nifi",
                "transportProtocol": "HTTP",
                "communicationsTimeout": "45 sec",
                "yieldDuration": "20 sec",
                "inputPorts": [
                    {
                        "identifier": "a0000000-0000-4000-8000-0000000000d2",
                        "name": "Remote Ingest",
                        "targetId": "f1000000-0000-4000-8000-000000000001",
                        "concurrentlySchedulableTaskCount": 2,
                        "useCompression": True,
                        "batchSize": {"count": 100},
                    }
                ],
                "outputPorts": [],
            }
        ],
        "processGroups": [],
        "variables": {},
        **root_extra,
    }
    return {"flowContents": root, "externalControllerServices": {}, "parameterContexts": {}, "flowEncodingVersion": "1.0"}


def _migrate(text: str, filename: str, source: str, target: str, fmt: str = "json_snapshot", accepted=None):
    doc = parse_flow_file(text, filename)
    analysis = analyze_migration(doc, source, target)
    ids = [s["id"] for s in analysis.suggestions()] if accepted == "all" else (accepted or [])
    return doc, analysis, generate_migrated_flow(doc, analysis, fmt, ids)


def _nodes(snapshot: dict) -> dict:
    root = snapshot["flowContents"]
    return {n["name"]: n for n in root["processors"] + root["controllerServices"]}


# --- Third-party NARs -----------------------------------------------------------


def test_third_party_bundles_keep_their_version_in_json_output():
    _, _, result = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "2.11.0")
    nodes = _nodes(result.migrated)
    assert nodes["Generate"]["bundle"]["version"] == "2.11.0"
    assert nodes["Custom"]["bundle"] == CUSTOM
    assert nodes["Lookup"]["bundle"] == CUSTOM
    restamped = {c["component"] for c in result.applied_changes if c["change"] == "bundleVersion"}
    assert restamped == {"Generate"}


def test_third_party_bundles_keep_their_version_through_an_xml_template():
    _, _, result = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "1.28.1", "xml_template")
    root = ET.fromstring(result.xml_text)
    versions = {
        p.findtext("name"): (p.findtext("bundle/group"), p.findtext("bundle/version"))
        for p in root.iter()
        if p.tag in ("processors", "controllerServices") and p.find("bundle") is not None
    }
    assert versions["Generate"] == ("org.apache.nifi", "1.28.1")
    assert versions["Custom"] == ("com.example", "3.4.1")
    assert versions["Lookup"] == ("com.example", "3.4.1")

    # And reading that template back for another target keeps them too.
    _, _, again = _migrate(result.xml_text, "t.xml", "1.28.1", "2.11.0")
    nodes = _nodes(again.migrated)
    assert nodes["Generate"]["bundle"]["version"] == "2.11.0"
    assert nodes["Custom"]["bundle"]["version"] == "3.4.1"


def test_detection_ignores_third_party_bundle_versions():
    flow = _flow()
    for i in range(5):
        extra = copy.deepcopy(flow["flowContents"]["processors"][1])
        extra["identifier"] = f"a0000000-0000-4000-8000-00000000010{i}"
        extra["name"] = f"Custom {i}"
        flow["flowContents"]["processors"].append(extra)
    doc = parse_flow_file(json.dumps(flow), "f.json")
    assert doc.detected_version == "1.19.1"


def test_a_flow_of_only_third_party_components_is_not_detected_as_their_version():
    flow = _flow()
    flow["flowContents"]["processors"] = flow["flowContents"]["processors"][1:]
    doc = parse_flow_file(json.dumps(flow), "f.json")
    assert doc.detected_version != "3.4.1"


# --- XML template round trip ------------------------------------------------------


def test_xml_output_keeps_every_field_the_reader_understands():
    _, _, result = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "1.28.1", "xml_template")
    back = source_snapshot(parse_flow_file(result.xml_text, "t.xml"))["flowContents"]

    custom = next(p for p in back["processors"] if p["name"] == "Custom")
    assert custom["concurrentlySchedulableTaskCount"] == 4
    assert custom["runDurationMillis"] == 25
    assert custom["retryCount"] == 3
    assert custom["backoffMechanism"] == "YIELD_PROCESSOR"
    assert custom["maxBackoffPeriod"] == "5 mins"
    assert custom["style"] == {"background-color": "#f0e68c"}
    assert custom["propertyDescriptors"]["Secret"]["sensitive"] is True

    assert back["controllerServices"][0]["annotationData"] == "<cfg/>"

    conn = next(c for c in back["connections"] if c["name"] == "enriched")
    assert conn["bends"] == [{"x": 200.0, "y": 360.0}]
    assert conn["prioritizers"] == ["org.apache.nifi.prioritizer.FirstInFirstOutPrioritizer"]
    assert (conn["loadBalanceStrategy"], conn["loadBalanceCompression"]) == ("ROUND_ROBIN", "COMPRESS_ATTRIBUTES_ONLY")
    assert (conn["labelIndex"], conn["zIndex"]) == (0, 2)

    port = back["inputPorts"][0]
    assert (port["comments"], port["concurrentlySchedulableTaskCount"]) == ("Entry point", 2)

    label = back["labels"][0]
    assert (label["width"], label["height"], label["style"]) == (240.0, 80.0, {"font-size": "14px"})


def test_xml_output_keeps_remote_process_groups_and_their_ports():
    _, _, result = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "1.28.1", "xml_template")
    back = source_snapshot(parse_flow_file(result.xml_text, "t.xml"))["flowContents"]

    [rpg] = back["remoteProcessGroups"]
    assert rpg["identifier"] == "a0000000-0000-4000-8000-0000000000d1"
    assert rpg["name"] == "Remote Site"
    assert rpg["targetUris"] == "https://remote.example:8443/nifi"
    assert (rpg["transportProtocol"], rpg["communicationsTimeout"], rpg["yieldDuration"]) == ("HTTP", "45 sec", "20 sec")
    [port] = rpg["inputPorts"]
    assert port["identifier"] == "a0000000-0000-4000-8000-0000000000d2"
    assert port["targetId"] == "f1000000-0000-4000-8000-000000000001"
    assert (port["concurrentlySchedulableTaskCount"], port["useCompression"]) == (2, True)
    assert port["batchSize"] == {"count": 100}

    to_remote = next(c for c in back["connections"] if c["destination"]["type"] == "REMOTE_INPUT_PORT")
    assert to_remote["destination"]["groupId"] == rpg["identifier"]


def test_old_targets_get_no_template_fields_they_predate():
    _, _, old = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "1.2.0", "xml_template")
    assert "<loadBalanceStrategy>" not in old.xml_text
    assert "<retryCount>" not in old.xml_text
    _, _, new = _migrate(json.dumps(_flow()), "f.json", "1.19.1", "1.16.3", "xml_template")
    assert "<loadBalanceStrategy>" in new.xml_text
    assert "<retryCount>" in new.xml_text


def test_a_label_next_to_a_lone_group_is_not_dropped():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<template encoding-version="1.3"><name>t</name><snippet>
  <labels><id>l1</id><label>Note</label><position><x>0</x><y>0</y></position></labels>
  <processGroups><id>g1</id><name>Group</name><contents/></processGroups>
</snippet></template>"""
    snapshot = source_snapshot(parse_flow_file(xml, "t.xml"))["flowContents"]
    assert [label["label"] for label in snapshot["labels"]] == ["Note"]
    assert [g["name"] for g in snapshot["processGroups"]] == ["Group"]


def test_a_template_without_a_group_id_gives_the_same_output_every_time(xml_template_1x):
    assert "<groupId>" not in xml_template_1x.split("<snippet>")[0]
    _, _, first = _migrate(xml_template_1x, "t.xml", "1.25.0", "1.2.0")
    _, _, second = _migrate(xml_template_1x, "t.xml", "1.25.0", "1.2.0")
    assert migrated_json_text(first) == migrated_json_text(second)


# --- Migrating a migrated file ------------------------------------------------------


def test_remigrating_replaces_the_provenance_note_instead_of_stacking_it(xml_template_1x):
    _, _, once = _migrate(xml_template_1x, "t.xml", "1.25.0", "1.28.1", "xml_template")
    _, _, twice = _migrate(once.xml_text, "t.xml", "1.28.1", "2.11.0")
    comments = twice.migrated["flowContents"]["comments"]
    assert comments.count("[Flowgenix migration]") == 1
    assert "NiFi 1.28.1 → 2.11.0" in comments
    assert comments.count("by Flowgenix") == 1


def test_remigrating_does_not_double_the_manual_review_banner(xml_template_1x):
    _, _, once = _migrate(xml_template_1x, "t.xml", "1.25.0", "2.0.0")
    _, _, twice = _migrate(migrated_json_text(once), "f.json", "2.0.0", "2.11.0")
    fetch = _nodes(twice.migrated)["Fetch Feed"]
    assert fetch["comments"].count("[MANUAL REVIEW REQUIRED") == 1


def test_a_stale_manual_review_banner_is_removed_and_reported(xml_template_1x):
    _, _, once = _migrate(xml_template_1x, "t.xml", "1.25.0", "2.0.0")
    assert "[MANUAL REVIEW REQUIRED" in _nodes(once.migrated)["Fetch Feed"]["comments"]

    # GetHTTP exists on 1.25.0, so going back needs no review.
    _, _, back = _migrate(migrated_json_text(once), "f.json", "2.0.0", "1.25.0")
    assert "[MANUAL REVIEW REQUIRED" not in _nodes(back.migrated)["Fetch Feed"].get("comments", "")
    assert any(c["change"] == "comments" and c["component"] == "Fetch Feed" for c in back.applied_changes)
