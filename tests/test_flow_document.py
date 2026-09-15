"""Tests for flow parsing and source-version detection.

The behaviour under most scrutiny here is the requirement that a .json upload is
never assumed to be NiFi 2.x, and that an undetectable version is reported as
undetectable instead of guessed.
"""

from __future__ import annotations

import pytest

from flow_document import (
    CONFIDENCE_CERTAIN,
    CONFIDENCE_LIKELY,
    CONFIDENCE_UNKNOWN,
    FORMAT_JSON_SNAPSHOT,
    FORMAT_STUDIO_SPEC,
    FORMAT_XML_TEMPLATE,
    FlowParseError,
    detected_line,
    parse_flow_file,
)


# --- Format dispatch ----------------------------------------------------------


def test_xml_template_is_recognised(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "legacy-intake.xml")
    assert doc.source_format == FORMAT_XML_TEMPLATE
    assert doc.root_name == "legacy-intake"


def test_json_flow_is_recognised(json_flow_2x):
    doc = parse_flow_file(json_flow_2x, "modern.json")
    assert doc.source_format == FORMAT_JSON_SNAPSHOT
    assert doc.root_name == "modern-flow"


def test_format_is_decided_by_content_not_extension(xml_template_1x):
    """Users rename downloads; a template named .json is still a template."""
    doc = parse_flow_file(xml_template_1x, "misnamed.json")
    assert doc.source_format == FORMAT_XML_TEMPLATE


def test_studio_spec_is_not_mistaken_for_a_nifi_export():
    spec = """
    {"processGroupName": "demo", "nifiVersion": "1.25.0",
     "processors": [{"name": "Gen", "type": "GenerateFlowFile", "properties": {}}]}
    """
    doc = parse_flow_file(spec, "demo.json")
    assert doc.source_format == FORMAT_STUDIO_SPEC
    assert doc.detected_version == "1.25.0"


def test_empty_upload_is_rejected():
    with pytest.raises(FlowParseError, match="empty"):
        parse_flow_file("   ", "x.json")


def test_non_json_non_xml_upload_is_rejected():
    with pytest.raises(FlowParseError, match="neither XML nor valid JSON"):
        parse_flow_file("id,name\n1,foo\n", "data.csv")


def test_json_that_is_not_a_flow_is_rejected():
    with pytest.raises(FlowParseError, match="flow definition"):
        parse_flow_file('{"hello": "world"}', "x.json")


def test_malformed_xml_is_rejected():
    with pytest.raises(FlowParseError, match="Malformed XML"):
        parse_flow_file("<template><unclosed>", "x.xml")


def test_unrecognised_xml_root_is_rejected():
    with pytest.raises(FlowParseError, match="Unrecognised XML root"):
        parse_flow_file("<beans><bean/></beans>", "x.xml")


def test_json_array_top_level_is_rejected_with_a_clear_message():
    with pytest.raises(FlowParseError, match="top level is a list"):
        parse_flow_file("[1, 2, 3]", "x.json")


# --- Component extraction -----------------------------------------------------


def test_xml_template_components_are_extracted(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "legacy-intake.xml")

    names = {p.name for p in doc.processors}
    assert names == {"Fetch Feed", "Reshape Records", "Write Result", "Log Attributes"}

    assert {s.short_type for s in doc.controller_services} == {
        "DistributedMapCacheClientService"
    }
    assert len(doc.of_kind("connection")) == 2
    assert len(doc.of_kind("funnel")) == 1
    assert len(doc.of_kind("port")) == 1


def test_xml_processor_properties_and_scheduling_are_captured(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "legacy-intake.xml")
    reshape = next(p for p in doc.processors if p.name == "Reshape Records")

    assert reshape.properties["Jolt Transformation DSL"] == "jolt-transform-shift"
    assert reshape.scheduling_strategy == "EVENT_DRIVEN"
    assert reshape.auto_terminated == ["failure"]
    assert reshape.short_type == "JoltTransformJSON"


def test_nested_process_groups_are_walked_with_a_path(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "legacy-intake.xml")
    nested = next(p for p in doc.processors if p.name == "Log Attributes")
    assert nested.group_path == "/Staging/"


def test_xml_group_variables_are_captured(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "legacy-intake.xml")
    assert {v["name"] for v in doc.variables} == {"outputRoot"}


def test_json_variables_are_captured(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "legacy.json")
    assert {v["name"] for v in doc.variables} == {"ingestHost"}


def test_json_parameter_contexts_are_captured(json_flow_2x):
    doc = parse_flow_file(json_flow_2x, "modern.json")
    assert [c["name"] for c in doc.parameter_contexts] == ["app-config"]
    assert set(doc.parameter_contexts[0]["parameters"]) == {"inputRoot", "outputRoot"}


def test_summary_reports_component_counts(xml_template_1x):
    summary = parse_flow_file(xml_template_1x, "t.xml").summary()
    assert summary["componentCounts"]["processor"] == 4
    assert summary["totalComponents"] == 9
    assert summary["variables"] == 1


# --- Version detection --------------------------------------------------------


def test_version_comes_from_the_nar_bundle_stamp(json_flow_1x):
    """Bundle version is NiFi's own record of the instance that exported the flow."""
    doc = parse_flow_file(json_flow_1x, "legacy.json")
    assert doc.detected_version == "1.23.2"
    assert doc.detection_confidence == CONFIDENCE_CERTAIN
    assert any("bundle version" in e for e in doc.detection_evidence)


def test_a_json_upload_is_not_assumed_to_be_2x(json_flow_1x):
    """The headline requirement: JSON does not imply NiFi 2.x."""
    doc = parse_flow_file(json_flow_1x, "legacy.json")
    assert detected_line(doc) == "1.x"
    assert doc.detected_version.startswith("1.")


def test_2x_json_is_detected_as_2x(json_flow_2x):
    doc = parse_flow_file(json_flow_2x, "modern.json")
    assert doc.detected_version == "2.11.0"
    assert detected_line(doc) == "2.x"


def test_xml_template_version_comes_from_bundles(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    assert doc.detected_version == "1.25.0"
    assert doc.detection_confidence == CONFIDENCE_CERTAIN


def test_undetectable_version_is_reported_not_guessed(json_flow_unversioned):
    """An honest 'unknown' is required so the UI can ask the user."""
    doc = parse_flow_file(json_flow_unversioned, "ambiguous.json")
    assert doc.detected_version is None
    assert doc.detection_confidence == CONFIDENCE_UNKNOWN
    assert any("cannot be inferred" in e for e in doc.detection_evidence)


def test_event_driven_scheduling_implies_the_1x_line():
    """EVENT_DRIVEN was removed in 2.0, so its presence pins the source to 1.x."""
    flow = """
    {"flowContents": {"name": "f", "processors": [
        {"identifier": "p1", "name": "P", "type": "org.apache.nifi.X",
         "schedulingStrategy": "EVENT_DRIVEN", "properties": {}}]}}
    """
    doc = parse_flow_file(flow, "f.json")
    assert doc.detection_confidence == CONFIDENCE_LIKELY
    assert detected_line(doc) == "1.x"


def test_execution_engine_marker_implies_the_2x_line():
    flow = """
    {"flowContents": {"name": "f", "executionEngine": "STANDARD", "processors": [
        {"identifier": "p1", "name": "P", "type": "org.apache.nifi.X", "properties": {}}]}}
    """
    doc = parse_flow_file(flow, "f.json")
    assert doc.detection_confidence == CONFIDENCE_LIKELY
    assert detected_line(doc) == "2.x"


def test_variables_block_implies_the_1x_line():
    flow = """
    {"flowContents": {"name": "f", "variables": {"a": "b"}, "processors": [
        {"identifier": "p1", "name": "P", "type": "org.apache.nifi.X", "properties": {}}]}}
    """
    doc = parse_flow_file(flow, "f.json")
    assert detected_line(doc) == "1.x"


def test_a_template_without_bundles_is_still_known_to_be_1x():
    """Templates only exist in 1.x, so the line is certain even with no stamps."""
    doc = parse_flow_file(
        "<template><name>t</name><snippet><processors><id>1</id><name>P</name>"
        "<type>org.apache.nifi.X</type></processors></snippet></template>",
        "t.xml",
    )
    assert doc.detected_version is None
    assert detected_line(doc) == "1.x"
    assert any("1.x-only feature" in e for e in doc.detection_evidence)


def test_original_payload_is_retained_for_non_destructive_migration(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "legacy.json")
    assert doc.original is not None
    assert doc.raw_text.strip().startswith("{")
