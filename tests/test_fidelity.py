from __future__ import annotations

import copy
from pathlib import Path

from fidelity import compare_flows
from flow_document import parse_flow_file
from migration_engine import analyze_migration, generate_migrated_flow

SAMPLE = Path(__file__).resolve().parent.parent / "ui" / "static" / "samples" / "legacy-intake.xml"


def _run(text: str, name: str, source: str, target: str = "2.6.0"):
    doc = parse_flow_file(text, name)
    analysis = analyze_migration(doc, source, target)
    accepted = [s["id"] for s in analysis.suggestions()]
    generation = generate_migrated_flow(doc, analysis, accepted=accepted)
    return doc, generation


def _failed(report: dict) -> list[str]:
    return [c["id"] for c in report["checks"] if not c["passed"]]


def test_sample_template_migrates_with_every_check_passing():
    doc, generation = _run(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name, "1.25.0")
    report = compare_flows(doc, generation.migrated, generation.applied_changes)
    assert report["source"] == "xml"
    assert _failed(report) == []
    assert report["allPassed"] and report["passed"] == report["total"]
    # The stranded funnel is reported as a property of the source, not a failure.
    assert any("funnel" in note for note in report["notes"])


def test_intended_property_rename_is_not_drift(xml_template_1x):
    doc, generation = _run(xml_template_1x, "t.xml", "1.25.0")
    renames = [c for c in generation.applied_changes if c["change"] in ("property", "property-removed")]
    assert renames, "fixture should exercise a rule-based property change"
    assert all(c.get("identifier") for c in generation.applied_changes)

    report = compare_flows(doc, generation.migrated, generation.applied_changes)
    assert _failed(report) == []
    # Without the change log the same output looks like lost properties.
    blind = compare_flows(doc, generation.migrated, [])
    assert "processor-properties" in _failed(blind)


def test_real_drift_is_caught(xml_template_1x):
    doc, generation = _run(xml_template_1x, "t.xml", "1.25.0")
    broken = copy.deepcopy(generation.migrated)
    proc = broken["flowContents"]["processors"][0]
    proc["position"] = {"x": 999.0, "y": 999.0}
    broken["flowContents"]["connections"][0]["destination"]["id"] = "nowhere"

    report = compare_flows(doc, broken, generation.applied_changes)
    failed = _failed(report)
    assert "processor-positions" in failed
    assert "connection-endpoints" in failed
    assert "no-dangling" in failed
    moved = next(c for c in report["checks"] if c["id"] == "processor-positions")
    assert moved["failed"] == 1 and "999" in moved["examples"][0]


def test_json_source_is_compared_too(json_flow_1x):
    doc, generation = _run(json_flow_1x, "flow.json", "1.25.0")
    report = compare_flows(doc, generation.migrated, generation.applied_changes)
    assert report["source"] == "json"
    assert _failed(report) == []
