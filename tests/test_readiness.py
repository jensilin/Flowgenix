from __future__ import annotations

import json
from pathlib import Path

from flow_document import parse_flow_file
from readiness import SEVERITY_ACTION, SEVERITY_CHECK, check_readiness

SAMPLE = Path(__file__).resolve().parent.parent / "ui" / "static" / "samples" / "legacy-intake.xml"


def _by_kind(report: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in report["items"]:
        out.setdefault(item["kind"], []).append(item)
    return out


def test_bundled_sample_reports_each_kind_of_problem():
    report = check_readiness(parse_flow_file(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name))
    kinds = _by_kind(report)

    assert [i["component"] for i in kinds["sensitive"]] == ["Fetch Feed"]
    assert kinds["sensitive"][0]["detail"] == "Password"

    assert [i["detail"] for i in kinds["path"]] == ["Directory = /data/out"]

    unhandled = kinds["relationship"]
    assert [(i["component"], i["detail"]) for i in unhandled] == [("Log Attributes", "success")]
    assert unhandled[0]["severity"] == SEVERITY_ACTION
    assert unhandled[0]["groupPath"] == "/Staging/"

    assert len(kinds["funnel"]) == 1
    assert report["relationshipsChecked"] is True
    assert report["counts"][SEVERITY_ACTION] == 1
    # Blocking items come first so the panel leads with them.
    assert report["items"][0]["severity"] == SEVERITY_ACTION


def test_relative_and_expression_paths_are_not_flagged(xml_template_1x):
    report = check_readiness(parse_flow_file(xml_template_1x, "t.xml"))
    flagged = [i["detail"] for i in report["items"] if i["kind"] == "path"]
    assert flagged == ["Directory = /data/out"]  # not ${outputRoot}/reshaped, not feed.json


def test_json_flow_without_relationship_lists_skips_that_check(json_flow_2x):
    report = check_readiness(parse_flow_file(json_flow_2x, "f.json"))
    assert report["relationshipsChecked"] is False
    assert not [i for i in report["items"] if i["kind"] == "relationship"]


def _flow_with_service_reference(ref: str, external: dict | None = None) -> str:
    return json.dumps(
        {
            "flowContents": {
                "identifier": "root",
                "name": "root",
                "componentType": "PROCESS_GROUP",
                "processors": [
                    {
                        "identifier": "p1",
                        "name": "Call API",
                        "type": "org.apache.nifi.processors.standard.InvokeHTTP",
                        "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "2.6.0"},
                        "properties": {"SSL Context Service": ref},
                        "propertyDescriptors": {
                            "SSL Context Service": {
                                "name": "SSL Context Service",
                                "identifiesControllerService": True,
                                "sensitive": False,
                            }
                        },
                        "autoTerminatedRelationships": [],
                    }
                ],
                "controllerServices": [{"identifier": "svc-in-file", "name": "Local SSL"}],
                "connections": [],
                "processGroups": [],
            },
            "externalControllerServices": external or {},
        }
    )


def test_service_reference_outside_the_file_must_be_created():
    report = check_readiness(parse_flow_file(_flow_with_service_reference("svc-gone"), "f.json"))
    [item] = [i for i in report["items"] if i["kind"] == "service"]
    assert item["severity"] == SEVERITY_ACTION
    assert item["detail"] == "SSL Context Service → svc-gone"


def test_external_service_is_resolved_by_name():
    external = {"svc-ext": {"identifier": "svc-ext", "name": "Shared SSL"}}
    report = check_readiness(parse_flow_file(_flow_with_service_reference("svc-ext", external), "f.json"))
    [item] = [i for i in report["items"] if i["kind"] == "service"]
    assert item["severity"] == SEVERITY_CHECK
    assert "Shared SSL" in item["detail"]


def test_service_in_the_file_is_fine():
    report = check_readiness(parse_flow_file(_flow_with_service_reference("svc-in-file"), "f.json"))
    assert not [i for i in report["items"] if i["kind"] == "service"]
