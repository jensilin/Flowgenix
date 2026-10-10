"""Prove the migrated flow still describes the same canvas as the source.

For an XML template the source side is read straight from the XML with its own
small reader, not through `versioned_flow`, so a bug in the converter cannot
hide itself. Changes the migration made on purpose (a renamed property, a
replaced processor type) are taken from `appliedChanges` and not counted as
drift.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any

from flow_document import FORMAT_XML_TEMPLATE, FlowDocument
from flow_graph import walk_groups
from flow_schema import xml_children

_MAX_EXAMPLES = 5


def compare_flows(
    doc: FlowDocument,
    migrated: dict[str, Any],
    applied_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if doc.source_format == FORMAT_XML_TEMPLATE and doc.xml_root is not None:
        snippet = doc.xml_root.find("snippet")
        source = xml_components(snippet if snippet is not None else doc.xml_root)
        source_kind = "xml"
    else:
        source = json_components(_json_root(doc.original))
        source_kind = "json"
    output_root = _json_root(migrated)
    produced = json_components(output_root)
    intended = _intended(applied_changes or [])

    checks: list[dict[str, Any]] = []

    def check(check_id: str, label: str, failures: list[str], checked: int, detail: str = "") -> None:
        checks.append(
            {
                "id": check_id,
                "label": label,
                "passed": not failures,
                "checked": checked,
                "failed": len(failures),
                "detail": detail,
                "examples": failures[:_MAX_EXAMPLES],
            }
        )

    for kind, label in (
        ("processors", "Processors"),
        ("connections", "Connections"),
        ("funnels", "Funnels"),
        ("ports", "Ports"),
        ("services", "Controller services"),
    ):
        a, b = len(source[kind]), len(produced[kind])
        check(
            f"{kind}-count",
            f"{label}: same count",
            [] if a == b else [f"source has {a}, output has {b}"],
            1,
            f"{a} → {b}",
        )

    output_group_ids = {g["id"] for g in produced["groups"]}
    lost_groups = [f"{g['name']} ({g['id'][:8]})" for g in source["groups"] if g["id"] not in output_group_ids]
    check("groups-survive", "Every process group survives", lost_groups, len(source["groups"]))

    by_id = {p["id"]: p for p in produced["processors"]}
    missing, renamed, retyped, lost_props, changed_values, moved = [], [], [], [], [], []
    for proc in source["processors"]:
        name = proc["name"] or proc["id"][:8]
        out = by_id.get(proc["id"])
        if out is None:
            missing.append(name)
            continue
        if out["name"] != proc["name"]:
            renamed.append(f"{proc['name']} → {out['name']}")
        expected_type = intended["types"].get(proc["id"], proc["type"])
        if out["type"] != expected_type:
            retyped.append(f"{name}: {_short(proc['type'])} → {_short(out['type'])}")
        renames = intended["renames"].get(proc["id"], {})
        removed = intended["removed"].get(proc["id"], set())
        for key, value in proc["properties"].items():
            if key in removed:
                continue
            new_key = renames.get(key, key)
            if new_key not in out["properties"]:
                lost_props.append(f"{name}: {key}")
            elif _norm(out["properties"][new_key]) != _norm(value):
                changed_values.append(f"{name}: {key}")
        if out["position"] != proc["position"]:
            moved.append(f"{name}: {proc['position']} → {out['position']}")
    total = len(source["processors"])
    check("processors-survive", "Every processor survives", missing, total)
    check("processor-names", "Processor names unchanged", renamed, total)
    check("processor-types", "Processor types unchanged, except intended replacements", retyped, total)
    check(
        "processor-properties",
        "Every property kept, except intended renames and removals",
        lost_props,
        sum(len(p["properties"]) for p in source["processors"]),
    )
    check(
        "property-values",
        "Property values unchanged",
        changed_values,
        sum(len(p["properties"]) for p in source["processors"]),
    )
    check("processor-positions", "Processor positions unchanged", moved, total)

    funnel_by_id = {f["id"]: f for f in produced["funnels"]}
    funnel_moved = []
    for funnel in source["funnels"]:
        out = funnel_by_id.get(funnel["id"])
        if out is None:
            funnel_moved.append(f"funnel {funnel['id'][:8]} missing")
        elif out["position"] != funnel["position"]:
            funnel_moved.append(f"funnel {funnel['id'][:8]}: {funnel['position']} → {out['position']}")
    check("funnel-positions", "Funnel positions unchanged", funnel_moved, len(source["funnels"]))

    conn_by_id = {c["id"]: c for c in produced["connections"]}
    rewired = []
    for conn in source["connections"]:
        label = conn["name"] or conn["id"][:8]
        out = conn_by_id.get(conn["id"])
        if out is None:
            rewired.append(f"{label} missing")
        elif (out["source"], out["destination"]) != (conn["source"], conn["destination"]):
            rewired.append(label)
    check("connection-endpoints", "Connection endpoints unchanged", rewired, len(source["connections"]))

    known = {p["id"] for p in produced["processors"]}
    known |= {f["id"] for f in produced["funnels"]}
    known |= {p["id"] for p in produced["ports"]}
    known |= output_group_ids
    known |= produced["remotePorts"]
    dangling = [
        c["name"] or c["id"][:8]
        for c in produced["connections"]
        if c["source"] not in known or c["destination"] not in known
    ]
    check(
        "no-dangling",
        "No connection points at a missing component",
        dangling,
        len(produced["connections"]),
    )

    orphans = [
        p["name"] or p["id"][:8]
        for p in produced["processors"]
        if p["groupIdentifier"] is not None and p["groupIdentifier"] not in output_group_ids
    ]
    check("no-orphans", "Every processor belongs to a group in the file", orphans, total)

    notes = []
    sources = {c["source"] for c in source["connections"]}
    stranded = [f["id"][:8] for f in source["funnels"] if f["id"] not in sources]
    if stranded:
        notes.append(
            f"The source already has {len(stranded)} funnel(s) with no outgoing connection "
            f"({', '.join(stranded)}). NiFi marks them invalid on import; this is not migration loss."
        )

    passed = sum(1 for c in checks if c["passed"])
    return {
        "source": source_kind,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "allPassed": passed == len(checks),
        "intended": {
            "retyped": len(intended["types"]),
            "renamedProperties": sum(len(v) for v in intended["renames"].values()),
            "removedProperties": sum(len(v) for v in intended["removed"].values()),
        },
        "notes": notes,
    }


# --- Readers ------------------------------------------------------------------


def _empty() -> dict[str, Any]:
    return {
        "processors": [],
        "connections": [],
        "funnels": [],
        "ports": [],
        "services": [],
        "groups": [],
        "remotePorts": set(),
    }


def xml_components(node: ET.Element, out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every component in a template snippet, read directly from the XML."""
    out = out if out is not None else _empty()
    for proc in xml_children(node, "processors"):
        config = proc.find("config")
        props: dict[str, Any] = {}
        for entry in config.findall("properties/entry") if config is not None else []:
            key = entry.find("key")
            value = entry.find("value")
            if key is not None and key.text:
                props[key.text] = value.text if value is not None else None
        out["processors"].append(
            {
                "id": _text(proc, "id"),
                "name": _text(proc, "name"),
                "type": _text(proc, "type"),
                "properties": props,
                "position": xml_position(proc),
                "groupIdentifier": None,
            }
        )
    for conn in xml_children(node, "connections"):
        out["connections"].append(
            {
                "id": _text(conn, "id"),
                "name": _text(conn, "name"),
                "source": _text(conn.find("source"), "id"),
                "destination": _text(conn.find("destination"), "id"),
            }
        )
    for funnel in xml_children(node, "funnels"):
        out["funnels"].append({"id": _text(funnel, "id"), "position": xml_position(funnel)})
    for tag in ("inputPorts", "outputPorts"):
        for port in xml_children(node, tag):
            out["ports"].append({"id": _text(port, "id"), "name": _text(port, "name")})
    for svc in xml_children(node, "controllerServices"):
        out["services"].append({"id": _text(svc, "id"), "name": _text(svc, "name")})
    for group in xml_children(node, "processGroups"):
        out["groups"].append({"id": _text(group, "id"), "name": _text(group, "name")})
        contents = group.find("contents")
        xml_components(contents if contents is not None else group, out)
    return out


def json_components(root: dict[str, Any]) -> dict[str, Any]:
    """Every component in a VersionedProcessGroup tree, root group included."""
    out = _empty()
    for group, _ in walk_groups(root):
        out["groups"].append({"id": _id(group), "name": str(group.get("name") or "")})
        for proc in group.get("processors") or []:
            out["processors"].append(
                {
                    "id": _id(proc),
                    "name": str(proc.get("name") or ""),
                    "type": str(proc.get("type") or ""),
                    "properties": proc.get("properties") or {},
                    "position": json_position(proc),
                    "groupIdentifier": proc.get("groupIdentifier"),
                }
            )
        for conn in group.get("connections") or []:
            out["connections"].append(
                {
                    "id": _id(conn),
                    "name": str(conn.get("name") or ""),
                    "source": str((conn.get("source") or {}).get("id") or ""),
                    "destination": str((conn.get("destination") or {}).get("id") or ""),
                }
            )
        for funnel in group.get("funnels") or []:
            out["funnels"].append({"id": _id(funnel), "position": json_position(funnel)})
        for key in ("inputPorts", "outputPorts"):
            for port in group.get(key) or []:
                out["ports"].append({"id": _id(port), "name": str(port.get("name") or "")})
        for svc in group.get("controllerServices") or []:
            out["services"].append({"id": _id(svc), "name": str(svc.get("name") or "")})
        for remote in group.get("remoteProcessGroups") or []:
            for key in ("inputPorts", "outputPorts"):
                out["remotePorts"] |= {_id(p) for p in remote.get(key) or [] if isinstance(p, dict)}
    return out


def xml_position(node: ET.Element) -> tuple[int, int]:
    pos = node.find("position")
    if pos is None:
        return (0, 0)
    return (round(float(_text(pos, "x") or 0)), round(float(_text(pos, "y") or 0)))


def json_position(node: dict[str, Any]) -> tuple[int, int]:
    pos = node.get("position") or {}
    return (round(float(pos.get("x") or 0)), round(float(pos.get("y") or 0)))


# --- Helpers ------------------------------------------------------------------


def _intended(applied_changes: list[dict[str, Any]]) -> dict[str, Any]:
    types: dict[str, str] = {}
    renames: dict[str, dict[str, str]] = defaultdict(dict)
    removed: dict[str, set[str]] = defaultdict(set)
    for change in applied_changes:
        ident = str(change.get("identifier") or "")
        if not ident:
            continue
        kind = change.get("change")
        if kind == "type" and change.get("to"):
            types[ident] = str(change["to"])
        elif kind == "property" and change.get("from") and change.get("to"):
            renames[ident][str(change["from"])] = str(change["to"])
        elif kind == "property-removed" and change.get("from"):
            removed[ident].add(str(change["from"]))
    return {"types": types, "renames": renames, "removed": removed}


def _json_root(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    for key in ("flowContents", "rootGroup"):
        if isinstance(data.get(key), dict):
            return data[key]
    return data if data.get("componentType") == "PROCESS_GROUP" else {}


def _norm(value: Any) -> Any:
    """Unset and blank compare equal; a lone newline is a real value."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "" and value not in ("\n", "\r\n", "\r"):
        return None
    return value


def _id(node: dict[str, Any]) -> str:
    return str(node.get("identifier") or node.get("id") or "")


def _text(node: ET.Element | None, child: str) -> str:
    if node is None:
        return ""
    found = node.find(child)
    return (found.text or "").strip() if found is not None and found.text else ""


def _short(type_name: str) -> str:
    return type_name.rsplit(".", 1)[-1]
