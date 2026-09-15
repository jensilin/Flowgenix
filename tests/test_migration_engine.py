"""Tests for migration analysis and migrated-flow generation.

Beyond correctness, these lock in the two safety guarantees the feature promises:
the uploaded file is never mutated, and a component that cannot be migrated
safely is never silently rewritten.
"""

from __future__ import annotations

import copy

import pytest

from flow_document import parse_flow_file
from migration_engine import (
    analyze_migration,
    generate_migrated_flow,
    migrated_json_text,
)
from migration_rules import COMPATIBLE, CONFIG_CHANGE, DEPRECATED, REMOVED, UNKNOWN


# --- Analysis -----------------------------------------------------------------


def test_every_component_gets_exactly_one_finding(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    assert len(analysis.findings) == len(doc.components)


def test_removed_processor_is_flagged_for_review(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    fetch = next(f for f in analysis.findings if f.name == "Fetch Feed")
    assert fetch.outcome == REMOVED
    assert fetch.manual_review is True
    assert fetch.target_type == "InvokeHTTP"
    assert "removed in NiFi 2.0" in fetch.explanation


def test_property_rename_is_detected(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    reshape = next(f for f in analysis.findings if f.name == "Reshape Records")
    renames = [c for c in reshape.property_changes if c["from"] == "Jolt Transformation DSL"]
    assert len(renames) == 1
    assert renames[0]["to"] == "Jolt Transform"


def test_event_driven_scheduling_is_rewritten_to_timer_driven(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    reshape = next(f for f in analysis.findings if f.name == "Reshape Records")
    sched = [c for c in reshape.property_changes if c["property"] == "schedulingStrategy"]
    assert len(sched) == 1
    assert sched[0]["from"] == "EVENT_DRIVEN"
    assert sched[0]["to"] == "TIMER_DRIVEN"


def test_event_driven_is_left_alone_for_a_1x_target(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.19.1", "1.25.0")

    reshape = next(f for f in analysis.findings if f.name == "Reshape Records")
    assert not [c for c in reshape.property_changes if c["property"] == "schedulingStrategy"]


def test_deprecated_service_is_reported(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    cache = next(f for f in analysis.findings if f.name == "Cache Client")
    assert cache.outcome == DEPRECATED


def test_structural_components_migrate_as_compatible(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    for kind in ("connection", "funnel", "port"):
        findings = [f for f in analysis.findings if f.kind == kind]
        assert findings
        assert all(f.outcome == COMPATIBLE for f in findings)


def test_summary_counts_add_up(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    summary = analyze_migration(doc, "1.25.0", "2.11.0").summary()

    assert summary["sourceVersion"] == "1.25.0"
    assert summary["targetVersion"] == "2.11.0"
    assert summary["totalComponents"] == len(doc.components)
    assert sum(summary["byOutcome"].values()) == summary["totalComponents"]
    assert summary["manualReview"] >= 1


def test_unsupported_target_version_is_rejected(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    with pytest.raises(ValueError, match="target"):
        analyze_migration(doc, "1.23.2", "9.9.9")


def test_downgrade_produces_a_warning(json_flow_2x):
    doc = parse_flow_file(json_flow_2x, "f.json")
    analysis = analyze_migration(doc, "2.11.0", "1.25.0")
    assert any("older than source" in w for w in analysis.warnings)


def test_variable_registry_removal_warns_and_names_the_broken_reference(xml_template_1x):
    """${outputRoot} in the fixture must be reported, since 2.x drops Variables."""
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    warning = next(w for w in analysis.warnings if "Variable Registry" in w)
    assert "outputRoot" in warning
    assert "#{" in warning  # tells the user the Parameter Context syntax


def test_no_variable_warning_when_targeting_1x(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.19.1", "1.25.0")
    assert not [w for w in analysis.warnings if "Variable Registry" in w]


# --- Catalog precedence -------------------------------------------------------


def test_live_catalog_confirms_a_processor_exists(json_flow_1x, fake_catalog_factory):
    catalog = fake_catalog_factory("2.11.0", {"getfile", "postfile"})
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0", target_catalog=catalog)

    read = next(f for f in analysis.findings if f.name == "Read Input")
    assert read.outcome == COMPATIBLE
    assert read.decided_by == "catalog"
    assert analysis.catalog_version == "2.11.0"


def test_live_catalog_marks_a_missing_processor_as_removed(fake_catalog_factory):
    """The catalog is ground truth: absent means absent, no rule required."""
    flow = """
    {"flowContents": {"name": "f", "processors": [
        {"identifier": "p1", "name": "Odd One", "type": "org.apache.nifi.custom.MyProc",
         "bundle": {"version": "1.25.0"}, "properties": {}}]}}
    """
    catalog = fake_catalog_factory("2.11.0", {"getfile"})
    doc = parse_flow_file(flow, "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0", target_catalog=catalog)

    finding = analysis.findings[0]
    assert finding.outcome == REMOVED
    assert finding.decided_by == "catalog"
    assert finding.manual_review is True


def test_mismatched_catalog_is_ignored_and_warned_about(json_flow_1x, fake_catalog_factory):
    """A catalog from the wrong version must not be trusted as the target."""
    catalog = fake_catalog_factory("1.25.0", {"getfile"})
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0", target_catalog=catalog)

    assert analysis.catalog_version is None
    assert any("the live catalog was ignored" in w for w in analysis.warnings)
    read = next(f for f in analysis.findings if f.name == "Read Input")
    assert read.decided_by != "catalog"


def test_curated_rule_wins_over_catalog_for_the_explanation(json_flow_1x, fake_catalog_factory):
    """PostHTTP absent from 2.x is known; the rule's explanation is richer than
    the catalog's bare 'not installed', so the rule supplies it."""
    catalog = fake_catalog_factory("2.11.0", {"getfile"})
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0", target_catalog=catalog)

    push = next(f for f in analysis.findings if f.name == "Push Downstream")
    assert push.outcome == REMOVED
    assert push.decided_by == "rules"
    assert push.target_type == "InvokeHTTP"


_UNCOVERED_FLOW = """
{"flowContents": {"name": "f", "processors": [
    {"identifier": "p1", "name": "Mystery", "type": "org.apache.nifi.custom.Mystery",
     "bundle": {"version": "1.25.0"}, "properties": {}}]}}
"""


def test_without_a_catalog_an_uncovered_processor_is_unknown_not_compatible():
    """A false 'compatible' is the dangerous answer, so we report uncertainty."""
    doc = parse_flow_file(_UNCOVERED_FLOW, "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    finding = analysis.findings[0]
    assert finding.outcome == UNKNOWN
    assert finding.decided_by == "default"
    assert "has not been verified" in finding.explanation


def test_unverified_is_not_counted_as_a_known_defect():
    """'We could not check' is a gap in our evidence, not a defect in the flow.

    Conflating the two would mark every component of every offline analysis as
    requiring manual review, which tells the user nothing.
    """
    doc = parse_flow_file(_UNCOVERED_FLOW, "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    finding = analysis.findings[0]
    assert finding.manual_review is False
    assert finding.block_auto_migration is False
    summary = analysis.summary()
    assert summary["unknown"] == 1
    assert summary["manualReview"] == 0


def test_unverified_does_not_block_a_deterministic_property_rename():
    """Otherwise an offline migration produces a flow in which nothing migrated."""
    flow = """
    {"flowContents": {"name": "f", "processors": [
        {"identifier": "p1", "name": "Reshape",
         "type": "org.apache.nifi.processors.standard.JoltTransformJSON",
         "bundle": {"version": "1.25.0"},
         "properties": {"Jolt Transformation DSL": "jolt-transform-shift"}}]}}
    """
    doc = parse_flow_file(flow, "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    node = result.migrated["flowContents"]["processors"][0]
    assert "Jolt Transform" in node["properties"]
    assert "Jolt Transformation DSL" not in node["properties"]


def test_unverified_component_carries_a_not_verified_note_in_the_output():
    doc = parse_flow_file(_UNCOVERED_FLOW, "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    node = result.migrated["flowContents"]["processors"][0]
    assert node["comments"].startswith("[NOT VERIFIED]")
    # A softer note than the manual-review banner, and not counted as skipped.
    assert "MANUAL REVIEW REQUIRED" not in node["comments"]
    assert not result.skipped


# --- Generation: the uploaded file is never mutated ---------------------------


def test_generation_does_not_mutate_the_parsed_document(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    before = copy.deepcopy(doc.original)

    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    generate_migrated_flow(doc, analysis)

    assert doc.original == before, "generation must work on a copy, never the upload"


def test_generation_does_not_mutate_the_raw_upload_text(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    raw_before = doc.raw_text
    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    generate_migrated_flow(doc, analysis)
    assert doc.raw_text == raw_before


# --- Generation: nothing unsafe is silently changed ---------------------------


def test_component_needing_review_is_left_unchanged_and_marked(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    node = next(
        p
        for p in result.migrated["flowContents"]["processors"]
        if p["name"] == "Push Downstream"
    )
    # Type is untouched despite a known replacement existing...
    assert node["type"].endswith("PostHTTP")
    # ...and the reason is written where a NiFi operator will see it.
    assert "MANUAL REVIEW REQUIRED" in node["comments"]
    assert any(m["component"] == "Push Downstream" for m in result.manual_markers)
    assert any(s["component"] == "Push Downstream" for s in result.skipped)


def test_manual_marker_records_the_suggested_replacement_without_applying_it(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    marker = next(m for m in result.manual_markers if m["component"] == "Push Downstream")
    assert marker["suggestedType"] == "InvokeHTTP"
    assert marker["outcome"] == REMOVED
    assert marker["reason"]


# --- Generation: safe changes are applied ------------------------------------


def test_safe_property_rename_is_applied(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    reshape = next(
        p for p in result.migrated["flowContents"]["processors"] if p["name"] == "Reshape Records"
    )
    assert "Jolt Transform" in reshape["properties"]
    assert "Jolt Transformation DSL" not in reshape["properties"]
    assert reshape["properties"]["Jolt Transform"] == "jolt-transform-shift"


def test_event_driven_is_rewritten_in_the_generated_flow(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    reshape = next(
        p for p in result.migrated["flowContents"]["processors"] if p["name"] == "Reshape Records"
    )
    assert reshape["schedulingStrategy"] == "TIMER_DRIVEN"


def test_bundle_versions_are_restamped_to_the_target(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    read = next(
        p for p in result.migrated["flowContents"]["processors"] if p["name"] == "Read Input"
    )
    assert read["bundle"]["version"] == "2.11.0"
    assert any(c["change"] == "bundleVersion" for c in result.applied_changes)


def test_every_applied_change_is_recorded_with_a_reason(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    assert result.applied_changes
    for change in result.applied_changes:
        assert change["component"]
        assert change["change"]
        assert "reason" in change


def test_variable_registry_block_is_cleared_and_reported(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    assert result.migrated["flowContents"]["variables"] == {}
    marker = next(m for m in result.manual_markers if m["reason"] == "variable_registry_removed")
    assert "ingestHost" in marker["detail"]


def test_variables_are_preserved_when_targeting_1x(json_flow_1x):
    doc = parse_flow_file(json_flow_1x, "f.json")
    analysis = analyze_migration(doc, "1.23.2", "1.25.0")
    result = generate_migrated_flow(doc, analysis)
    assert result.migrated["flowContents"]["variables"] == {"ingestHost": "downstream.test"}


# --- Generation: XML template → JSON flow definition -------------------------


def test_xml_template_is_converted_to_a_json_flow_definition(xml_template_1x):
    """NiFi 2.x cannot import templates, so the output must be a flow definition."""
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    assert result.output_format == "json_snapshot"
    assert "flowContents" in result.migrated
    contents = result.migrated["flowContents"]
    assert len(contents["connections"]) == 2
    assert len(contents["inputPorts"]) == 1
    assert len(contents["funnels"]) == 1

    # The nested group stays nested rather than being hoisted into the root,
    # so 3 processors sit at the root and the 4th stays in its own group.
    assert len(contents["processors"]) == 3
    assert len(contents["processGroups"]) == 1
    assert [p["name"] for p in contents["processGroups"][0]["processors"]] == ["Log Attributes"]


def test_template_conversion_explains_itself_in_the_flow_comments(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)
    assert "cannot import XML templates" in result.migrated["flowContents"]["comments"]


def test_generated_flow_records_its_provenance_in_comments(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    comments = result.migrated["flowContents"]["comments"]
    assert "1.25.0" in comments and "2.11.0" in comments
    assert "t.xml" in comments


def test_no_unknown_top_level_keys_are_emitted(xml_template_1x):
    """NiFi rejects an entire flow definition containing a field it does not
    recognise, so the artifact must carry no metadata of our own invention."""
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    allowed = {
        "flowContents",
        "externalControllerServices",
        "parameterContexts",
        "parameterProviders",
        "flowEncodingVersion",
        "latest",
    }
    assert set(result.migrated) <= allowed, f"unexpected keys: {set(result.migrated) - allowed}"


def test_migrated_flow_serialises_to_valid_json(xml_template_1x):
    import json

    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    text = migrated_json_text(generate_migrated_flow(doc, analysis))
    assert json.loads(text)["flowContents"]["name"] == "legacy-intake"


def test_studio_spec_migration_stays_in_studio_spec_format():
    spec = """
    {"processGroupName": "demo", "nifiVersion": "1.25.0", "processors": [
        {"name": "Reshape", "type": "JoltTransformJSON",
         "properties": {"Jolt Transformation DSL": "jolt-transform-shift"}}]}
    """
    doc = parse_flow_file(spec, "demo.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    result = generate_migrated_flow(doc, analysis)

    assert result.output_format == "studio_spec"
    assert result.migrated["nifiVersion"] == "2.11.0"
    assert "Jolt Transform" in result.migrated["processors"][0]["properties"]
