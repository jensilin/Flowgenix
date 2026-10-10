from __future__ import annotations

from pathlib import Path

from flow_document import parse_flow_file
from flow_preview import build_preview
from migration_engine import analyze_migration

SAMPLE = Path(__file__).resolve().parent.parent / "ui" / "static" / "samples" / "legacy-intake.xml"


def _sample_preview():
    doc = parse_flow_file(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)
    analysis = analyze_migration(doc, "1.25.0", "2.6.0")
    return build_preview(doc, {f.identifier: f.outcome for f in analysis.findings})


def test_groups_are_flat_and_hold_only_their_own_canvas():
    preview = _sample_preview()
    root = preview["groups"][preview["root"]]
    assert root["parent"] is None
    assert {p["name"] for p in root["processors"]} == {"Fetch Feed", "Reshape Records", "Write Result"}
    assert [c["name"] for c in root["children"]] == ["Staging"]

    staging = preview["groups"][root["children"][0]["id"]]
    assert staging["parent"] == preview["root"]
    assert staging["path"] == "/Staging/"
    assert [p["name"] for p in staging["processors"]] == ["Log Attributes"]


def test_positions_types_and_connections_come_through():
    root = (p := _sample_preview())["groups"][p["root"]]
    write = next(x for x in root["processors"] if x["name"] == "Write Result")
    assert (write["x"], write["y"]) == (0.0, 480.0)
    assert write["type"] == "PutFile"
    edges = {(c["source"]["id"][-1], c["destination"]["id"][-1]) for c in root["connections"]}
    assert edges == {("1", "2"), ("2", "3")}
    assert all(c["relationships"] == ["success"] for c in root["connections"])
    assert root["funnels"] and root["ports"][0]["kind"] == "input"


def test_outcomes_colour_processors_and_roll_up_to_groups():
    preview = _sample_preview()
    root = preview["groups"][preview["root"]]
    fetch = next(x for x in root["processors"] if x["name"] == "Fetch Feed")
    assert fetch["outcome"] == "removed"  # GetHTTP is gone in 2.x
    # The root contains a removed processor, so its rolled-up outcome is the worst one.
    assert root["outcome"] == "removed"
    assert root["processorCount"] == 4


def test_json_flow_preview(json_flow_2x):
    preview = build_preview(parse_flow_file(json_flow_2x, "flow.json"))
    assert preview["root"] in preview["groups"]
    assert preview["sizes"]["processor"] == (352, 128)
