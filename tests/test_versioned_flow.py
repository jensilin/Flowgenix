"""Tests for the NiFi flow-definition writer.

These exist because NiFi reports every structural defect in an imported flow
definition as the same message — "An unexpected error has occurred. Please check
the logs for additional details." — which gives a user no way to tell a missing
`componentType` from a detached connection. The checks below assert the shape
directly so the failure surfaces here instead.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from flow_document import parse_flow_file
from migration_engine import analyze_migration, generate_migrated_flow
from versioned_flow import snapshot_from_xml_template

#: Keys NiFi's VersionedFlowSnapshot model recognises at the top level.
SNAPSHOT_KEYS = {
    "flowContents",
    "externalControllerServices",
    "parameterContexts",
    "parameterProviders",
    "flowEncodingVersion",
    "latest",
}


def _migrate(xml_text: str, target: str = "2.11.0"):
    doc = parse_flow_file(xml_text, "t.xml")
    analysis = analyze_migration(doc, doc.detected_version or "1.25.0", target)
    return generate_migrated_flow(doc, analysis).migrated


def _walk(group, out=None):
    """Every process group in the tree, root first."""
    out = out if out is not None else []
    out.append(group)
    for child in group.get("processGroups") or []:
        _walk(child, out)
    return out


# --- Snapshot envelope --------------------------------------------------------


def test_snapshot_has_only_recognised_top_level_keys(xml_template_1x):
    migrated = _migrate(xml_template_1x)
    assert set(migrated) <= SNAPSHOT_KEYS


def test_snapshot_declares_its_encoding_version(xml_template_1x):
    assert _migrate(xml_template_1x)["flowEncodingVersion"] == "1.0"


def test_root_is_a_process_group_with_a_position(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    assert root["componentType"] == "PROCESS_GROUP"
    assert set(root["position"]) == {"x", "y"}
    assert root["identifier"]


# --- Required fields on every component ---------------------------------------


def test_every_component_declares_identifier_type_and_position(xml_template_1x):
    migrated = _migrate(xml_template_1x)
    expected = {
        "processors": "PROCESSOR",
        "controllerServices": "CONTROLLER_SERVICE",
        "funnels": "FUNNEL",
        "labels": "LABEL",
    }
    seen = 0
    for group in _walk(migrated["flowContents"]):
        for key, component_type in expected.items():
            for node in group.get(key) or []:
                seen += 1
                assert node["identifier"], f"{key} entry has no identifier"
                assert node["componentType"] == component_type
                assert node["groupIdentifier"] == group["identifier"]
                if key != "controllerServices":
                    assert set(node["position"]) == {"x", "y"}
    assert seen > 0


def test_processors_carry_the_fields_nifi_requires(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    for proc in root["processors"]:
        for field in (
            "type",
            "bundle",
            "properties",
            "schedulingStrategy",
            "schedulingPeriod",
            "relationships",
            "autoTerminatedRelationships",
            "scheduledState",
            "penaltyDuration",
            "yieldDuration",
            "bulletinLevel",
        ):
            assert field in proc, f"processor {proc['name']} is missing {field}"
        assert set(proc["bundle"]) == {"group", "artifact", "version"}


def test_ports_declare_their_direction(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    for port in root["inputPorts"]:
        assert port["type"] == "INPUT_PORT"
        assert port["componentType"] == "INPUT_PORT"


# --- Connections: the defect that broke the real-world import -----------------


def test_connection_endpoints_are_connectable_objects(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    assert root["connections"]
    for conn in root["connections"]:
        for end in ("source", "destination"):
            assert isinstance(conn[end], dict), "endpoint must be a ConnectableComponent"
            assert set(conn[end]) >= {"id", "type", "groupId"}
        assert conn["componentType"] == "CONNECTION"


def test_connection_endpoints_are_populated_not_empty(xml_template_1x):
    """A template nests endpoints under <source>/<destination>; reading the flat
    <sourceId> that does not exist yields empty ids and silently detaches every
    connection while the component count still looks correct."""
    root = _migrate(xml_template_1x)["flowContents"]
    for conn in root["connections"]:
        assert conn["source"]["id"], f"connection {conn['name']} has no source id"
        assert conn["destination"]["id"], f"connection {conn['name']} has no destination id"


def test_every_connection_resolves_to_a_component_in_the_flow(xml_template_1x):
    migrated = _migrate(xml_template_1x)
    ids = set()
    connections = []
    for group in _walk(migrated["flowContents"]):
        for key in ("processors", "inputPorts", "outputPorts", "funnels"):
            for node in group.get(key) or []:
                ids.add(node["identifier"])
        connections.extend(group.get("connections") or [])

    assert connections
    for conn in connections:
        assert conn["source"]["id"] in ids, f"{conn['name']} source is dangling"
        assert conn["destination"]["id"] in ids, f"{conn['name']} destination is dangling"


def test_selected_relationships_survive(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    assert any(c["selectedRelationships"] for c in root["connections"])


# --- Structure and fidelity ---------------------------------------------------


def test_a_single_wrapped_group_is_promoted_to_the_root():
    """"Download template" on a group wraps it in a snippet; nesting that again
    would bury the flow a level deeper than the author drew it.

    The template `<groupId>` is the parent canvas, not the group. Children must
    keep pointing at the group's own id or NiFi opens a disconnected canvas.
    """
    xml = """<template encoding-version="1.3">
      <groupId>parent-canvas</groupId><name>outer</name><snippet>
      <processGroups><id>g1</id><name>Real Flow</name><contents>
        <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
          <position><x>10</x><y>20</y></position></processors>
        <connections><id>c1</id>
          <source><id>p1</id><groupId>g1</groupId><type>PROCESSOR</type></source>
          <destination><id>p1</id><groupId>g1</groupId><type>PROCESSOR</type></destination>
          <selectedRelationships>success</selectedRelationships>
        </connections>
      </contents></processGroups>
    </snippet></template>"""
    root = _migrate(xml)["flowContents"]
    assert root["name"] == "Real Flow"
    assert root["identifier"] == "g1"
    assert root["identifier"] != "parent-canvas"
    assert len(root["processors"]) == 1
    assert root["processGroups"] == []
    assert root["processors"][0]["groupIdentifier"] == "g1"
    assert root["connections"][0]["groupIdentifier"] == "g1"
    assert root["connections"][0]["source"]["name"] == "P"


def test_nested_groups_are_preserved_not_flattened():
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p0</id><name>Top</name><type>org.apache.nifi.X</type></processors>
      <processGroups><id>g1</id><name>Child</name><contents>
        <processors><id>p1</id><name>Inner</name><type>org.apache.nifi.Y</type></processors>
      </contents></processGroups>
    </snippet></template>"""
    root = _migrate(xml)["flowContents"]
    assert [p["name"] for p in root["processors"]] == ["Top"]
    assert len(root["processGroups"]) == 1
    child = root["processGroups"][0]
    assert child["name"] == "Child"
    assert [p["name"] for p in child["processors"]] == ["Inner"]
    assert child["processors"][0]["groupIdentifier"] == child["identifier"]


def test_positions_are_preserved_from_the_template():
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <position><x>1344.0</x><y>136.0</y></position></processors>
    </snippet></template>"""
    proc = _migrate(xml)["flowContents"]["processors"][0]
    assert proc["position"] == {"x": 1344.0, "y": 136.0}


def test_auto_terminate_flags_come_from_the_relationships_block():
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <relationships><name>failure</name><autoTerminate>true</autoTerminate></relationships>
        <relationships><name>success</name><autoTerminate>false</autoTerminate></relationships>
      </processors>
    </snippet></template>"""
    proc = _migrate(xml)["flowContents"]["processors"][0]
    assert proc["autoTerminatedRelationships"] == ["failure"]
    names = {r["name"]: r["autoTerminate"] for r in proc["relationships"]}
    assert names == {"failure": True, "success": False}


def test_bundle_version_is_restamped_to_the_target(xml_template_1x):
    root = _migrate(xml_template_1x)["flowContents"]
    for proc in root["processors"]:
        assert proc["bundle"]["version"] == "2.11.0"


def test_stopped_processors_are_enabled_not_disabled():
    """NiFi does not auto-start an imported flow definition. Mapping STOPPED to
    DISABLED greys out every processor and is not how the template was drawn."""
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <state>STOPPED</state></processors>
      <processors><id>p2</id><name>Off</name><type>org.apache.nifi.Y</type>
        <state>DISABLED</state></processors>
    </snippet></template>"""
    root = _migrate(xml)["flowContents"]
    by_name = {p["name"]: p["scheduledState"] for p in root["processors"]}
    assert by_name == {"P": "ENABLED", "Off": "DISABLED"}


def test_unset_property_is_null_not_empty_string():
    """"" is a real value for some NiFi properties, so an unset one must not
    become one."""
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <config><properties>
          <entry><key>Set</key><value>v</value></entry>
          <entry><key>Unset</key></entry>
        </properties></config>
      </processors>
    </snippet></template>"""
    props = _migrate(xml)["flowContents"]["processors"][0]["properties"]
    assert props["Set"] == "v"
    assert props["Unset"] is None


def test_newline_only_property_value_is_preserved():
    """MergeContent's demarcator is often a lone newline, serialized as
    `<value>\\n</value>`. Pretty-printed empty values must not become that."""
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <config><properties>
          <entry><key>Demarcator File</key><value>
</value></entry>
          <entry><key>Header File</key><value>
                            </value></entry>
        </properties></config>
      </processors>
    </snippet></template>"""
    props = _migrate(xml)["flowContents"]["processors"][0]["properties"]
    assert props["Demarcator File"] == "\n"
    assert props["Header File"] is None


def test_property_descriptors_are_copied_from_the_template():
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type>
        <config><descriptors>
          <entry><key>Input Directory</key><value><name>Input Directory</name></value></entry>
        </descriptors>
        <properties><entry><key>Input Directory</key><value>/data</value></entry></properties>
        </config>
      </processors>
    </snippet></template>"""
    proc = _migrate(xml)["flowContents"]["processors"][0]
    desc = proc["propertyDescriptors"]["Input Directory"]
    assert desc["name"] == "Input Directory"
    assert desc["displayName"] == "Input Directory"


def test_writer_runs_without_a_migration_callback():
    xml = """<template encoding-version="1.3"><name>t</name><snippet>
      <processors><id>p1</id><name>P</name><type>org.apache.nifi.X</type></processors>
    </snippet></template>"""
    snapshot = snapshot_from_xml_template(ET.fromstring(xml), "2.11.0")
    assert snapshot["flowContents"]["processors"][0]["componentType"] == "PROCESSOR"
