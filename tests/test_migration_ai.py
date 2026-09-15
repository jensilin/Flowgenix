"""Tests for the AI layer and the report renderer.

The critical property under test: AI output is advisory. It may annotate a
finding but must never change a deterministic verdict, and must never reach the
generated flow.
"""

from __future__ import annotations

import json

from flow_document import parse_flow_file
from migration_ai import (
    apply_ai_suggestions,
    build_migration_prompt,
    build_report,
    needs_ai_review,
    parse_ai_response,
    render_report_markdown,
)
from migration_engine import analyze_migration, generate_migrated_flow


# --- Selecting what to ask about ----------------------------------------------


def test_only_unresolved_findings_are_sent_for_ai_review(xml_template_1x, fake_catalog_factory):
    """A component the catalog already settled must not be sent for a second opinion."""
    catalog = fake_catalog_factory(
        "2.11.0", {"putfile", "logattribute", "jolttransformjson"}, {"distributedmapcacheclientservice"}
    )
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0", target_catalog=catalog)
    pending = needs_ai_review(analysis)

    assert pending
    for finding in pending:
        assert finding.manual_review or finding.outcome in ("removed", "unknown", "manual_review")
    # PutFile is confirmed installed by the catalog, so it is settled.
    assert not any(f.name == "Write Result" for f in pending)
    # GetHTTP was removed in 2.0, so it still goes to the model.
    assert any(f.name == "Fetch Feed" for f in pending)


def test_unverified_components_are_sent_to_ai_even_though_not_flagged_as_defects(
    xml_template_1x,
):
    """Without a catalog, 'unknown' is exactly where a model helps most."""
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    assert any(f.outcome == "unknown" for f in analysis.findings)
    assert any(f.outcome == "unknown" for f in needs_ai_review(analysis))


def test_duplicate_types_are_deduplicated_before_prompting():
    flow = {
        "flowContents": {
            "name": "f",
            "processors": [
                {
                    "identifier": f"p{i}",
                    "name": f"Fetch {i}",
                    "type": "org.apache.nifi.processors.standard.GetHTTP",
                    "bundle": {"version": "1.25.0"},
                    "properties": {},
                }
                for i in range(5)
            ],
        }
    }
    doc = parse_flow_file(json.dumps(flow), "f.json")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    assert len([f for f in analysis.findings if f.outcome == "removed"]) == 5
    assert len(needs_ai_review(analysis)) == 1


def test_prompt_states_that_deterministic_verdicts_are_authoritative(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    prompt = build_migration_prompt(analysis, needs_ai_review(analysis))

    assert "must not contradict" in prompt
    assert "1.25.0" in prompt and "2.11.0" in prompt
    assert "Do not guess" in prompt


def test_prompt_constrains_replacements_to_installed_types(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    prompt = build_migration_prompt(
        analysis,
        needs_ai_review(analysis),
        catalog_summary={"allowedProcessorTypes": ["org.apache.nifi.processors.standard.InvokeHTTP"]},
    )
    assert "Only propose replacements from this list" in prompt
    assert "InvokeHTTP" in prompt


# --- Parsing the reply --------------------------------------------------------


def test_plain_json_array_is_parsed():
    parsed = parse_ai_response('[{"sourceType": "GetHTTP", "existsInTarget": false}]')
    assert parsed[0]["sourceType"] == "GetHTTP"


def test_code_fenced_json_is_parsed():
    parsed = parse_ai_response('```json\n[{"sourceType": "GetHTTP"}]\n```')
    assert parsed[0]["sourceType"] == "GetHTTP"


def test_json_wrapped_in_prose_is_recovered():
    parsed = parse_ai_response('Sure! Here you go:\n[{"sourceType": "GetHTTP"}]\nHope that helps.')
    assert parsed[0]["sourceType"] == "GetHTTP"


def test_malformed_reply_degrades_to_empty_rather_than_raising():
    """A bad AI reply must not fail an analysis that is already valid."""
    assert parse_ai_response("I'm not sure, sorry.") == []
    assert parse_ai_response("[{unclosed") == []
    assert parse_ai_response("") == []


def test_non_dict_entries_are_discarded():
    assert parse_ai_response('["a string", {"sourceType": "X"}]') == [{"sourceType": "X"}]


# --- Applying suggestions -----------------------------------------------------


def test_suggestions_annotate_without_changing_the_deterministic_outcome(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    fetch = next(f for f in analysis.findings if f.name == "Fetch Feed")
    outcome_before = fetch.outcome
    target_before = fetch.target_type

    apply_ai_suggestions(
        analysis,
        [
            {
                "sourceType": "org.apache.nifi.processors.standard.GetHTTP",
                "existsInTarget": False,
                "recommendation": "Use InvokeHTTP with method GET.",
                "suggestedReplacement": "ListenHTTP",
                "confidence": "medium",
            }
        ],
    )

    assert fetch.outcome == outcome_before
    # target_type drives generation, so an AI suggestion must not touch it —
    # even a wrong one like ListenHTTP above.
    assert fetch.target_type == target_before
    assert "InvokeHTTP with method GET" in fetch.ai_suggestion
    assert "ListenHTTP" in fetch.ai_suggestion  # visible as advice only


def test_suggestions_match_on_short_name_too(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    annotated = apply_ai_suggestions(
        analysis, [{"sourceType": "GetHTTP", "recommendation": "Short-name match works."}]
    )
    assert annotated >= 1
    fetch = next(f for f in analysis.findings if f.name == "Fetch Feed")
    assert "Short-name match works." in fetch.ai_suggestion


def test_new_required_properties_are_merged(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    apply_ai_suggestions(
        analysis, [{"sourceType": "GetHTTP", "newRequiredProperties": ["HTTP Method"]}]
    )
    fetch = next(f for f in analysis.findings if f.name == "Fetch Feed")
    assert "HTTP Method" in fetch.new_required_properties


def test_ai_property_suggestions_are_labelled_as_suggestions(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")

    apply_ai_suggestions(
        analysis,
        [
            {
                "sourceType": "GetHTTP",
                "propertyChanges": [{"from": "URL", "to": "Remote URL", "reason": "renamed"}],
            }
        ],
    )
    fetch = next(f for f in analysis.findings if f.name == "Fetch Feed")
    change = next(c for c in fetch.property_changes if c["from"] == "URL")
    assert change["reason"].startswith("AI suggestion")


def test_ai_suggestions_never_reach_the_generated_flow(xml_template_1x):
    """The end-to-end safety property: AI text is display-only."""
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    apply_ai_suggestions(
        analysis,
        [
            {
                "sourceType": "GetHTTP",
                "suggestedReplacement": "TotallyMadeUpProcessor",
                "recommendation": "Swap it out.",
            }
        ],
    )
    result = generate_migrated_flow(doc, analysis)

    serialized = json.dumps(result.migrated)
    assert "TotallyMadeUpProcessor" not in serialized
    fetch = next(
        p for p in result.migrated["flowContents"]["processors"] if p["name"] == "Fetch Feed"
    )
    assert fetch["type"].endswith("GetHTTP")


def test_empty_suggestions_are_a_no_op(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    assert apply_ai_suggestions(analysis, []) == 0


# --- Report -------------------------------------------------------------------


def test_report_contains_every_required_section(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    generation = generate_migrated_flow(doc, analysis)
    report = build_report(analysis, generation, ai_used="composer-2.5")

    for key in (
        "sourceVersion",
        "targetVersion",
        "sourceFormat",
        "summary",
        "components",
        "propertyChanges",
        "manualInterventionRequired",
        "flowLevelConcerns",
        "aiRecommendations",
        "warnings",
        "errors",
        "generation",
    ):
        assert key in report, f"report is missing {key}"

    assert report["aiModel"] == "composer-2.5"
    for bucket in ("compatible", "configChanges", "replaced", "deprecated", "removed"):
        assert bucket in report["components"]


def test_report_is_json_serialisable(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    report = build_report(analysis, generate_migrated_flow(doc, analysis))
    assert json.loads(json.dumps(report))["report"] == "NiFi Migration Report"


def test_markdown_report_renders_the_key_sections(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    generation = generate_migrated_flow(doc, analysis)
    md = render_report_markdown(build_report(analysis, generation, ai_used="composer-2.5"))

    assert md.startswith("# NiFi Migration Report")
    assert "**Source version:** 1.25.0" in md
    assert "**Target version:** 2.11.0" in md
    assert "## Summary" in md
    assert "## Manual intervention required" in md
    assert "## Flow-level concerns" in md
    assert "Fetch Feed" in md


def test_markdown_report_escapes_pipes_so_tables_do_not_break():
    report = {
        "summary": {},
        "sourceVersion": "1.25.0",
        "targetVersion": "2.11.0",
        "manualInterventionRequired": [
            {
                "name": "Weird",
                "sourceType": "org.apache.nifi.X",
                "outcome": "removed",
                "explanation": "has a | pipe and a\nnewline",
            }
        ],
        "components": {},
    }
    md = render_report_markdown(report)
    assert "\\|" in md
    row = next(line for line in md.splitlines() if "Weird" in line)
    assert "\n" not in row


def test_markdown_report_says_when_ai_was_not_used(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    md = render_report_markdown(build_report(analysis, None, ai_used=None))
    assert "**AI model:** not used" in md


def test_report_notes_when_no_live_catalog_verified_the_analysis(xml_template_1x):
    doc = parse_flow_file(xml_template_1x, "t.xml")
    analysis = analyze_migration(doc, "1.25.0", "2.11.0")
    md = render_report_markdown(build_report(analysis, None))
    assert "rule base only" in md
