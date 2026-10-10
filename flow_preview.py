"""A compact, render-ready outline of the source canvas.

The browser draws one process group at a time, so the outline is a flat map
of groups, each holding only what sits directly on its canvas. Positions are
NiFi's own canvas coordinates; sizes are NiFi's default component sizes.
"""

from __future__ import annotations

from typing import Any

from flow_document import FlowDocument
from flow_graph import component_id, items, root_group, source_snapshot

#: Width and height NiFi draws each component at.
SIZES = {
    "processor": (352, 128),
    "group": (384, 176),
    "port": (240, 48),
    "funnel": (48, 48),
    "remote": (384, 176),
}


def build_preview(doc: FlowDocument, outcomes: dict[str, str] | None = None) -> dict[str, Any]:
    """`outcomes` maps component id -> migration outcome for colouring."""
    outcomes = outcomes or {}
    root = root_group(source_snapshot(doc))
    groups: dict[str, dict[str, Any]] = {}
    root_id = _visit(root, None, "/", groups, outcomes)
    return {"root": root_id, "groups": groups, "sizes": SIZES}


def _visit(
    group: dict[str, Any],
    parent: str | None,
    path: str,
    groups: dict[str, dict[str, Any]],
    outcomes: dict[str, str],
) -> str:
    gid = component_id(group) or f"group-{len(groups)}"
    entry: dict[str, Any] = {
        "id": gid,
        "name": str(group.get("name") or ("Root" if parent is None else "group")),
        "parent": parent,
        "path": path,
        "processors": [
            {
                "id": component_id(p),
                "name": str(p.get("name") or ""),
                "type": str(p.get("type") or "").rsplit(".", 1)[-1],
                **_xy(p),
                "outcome": outcomes.get(component_id(p), ""),
            }
            for p in items(group, "processors")
        ],
        "ports": [
            {"id": component_id(p), "name": str(p.get("name") or ""), "kind": kind, **_xy(p)}
            for key, kind in (("inputPorts", "input"), ("outputPorts", "output"))
            for p in items(group, key)
        ],
        "funnels": [{"id": component_id(f), **_xy(f)} for f in items(group, "funnels")],
        "labels": [
            {
                "id": component_id(label),
                "text": str(label.get("label") or ""),
                "w": _num(label.get("width"), 150),
                "h": _num(label.get("height"), 50),
                **_xy(label),
            }
            for label in items(group, "labels")
        ],
        "remotes": [
            {
                "id": component_id(r),
                "name": str(r.get("name") or r.get("targetUris") or r.get("targetUri") or "remote"),
                **_xy(r),
            }
            for r in items(group, "remoteProcessGroups")
        ],
        "connections": [
            {
                "id": component_id(c),
                "source": _endpoint(c.get("source")),
                "destination": _endpoint(c.get("destination")),
                "relationships": [str(r) for r in c.get("selectedRelationships") or []],
                "name": str(c.get("name") or ""),
                "bends": [_xy({"position": b}) for b in c.get("bends") or [] if isinstance(b, dict)],
            }
            for c in items(group, "connections")
        ],
        "children": [],
    }
    groups[gid] = entry

    total = len(entry["processors"])
    worst = _worst(p["outcome"] for p in entry["processors"])
    for child in items(group, "processGroups"):
        child_path = f"{path}{child.get('name') or 'group'}/"
        cid = _visit(child, gid, child_path, groups, outcomes)
        summary = groups[cid]
        entry["children"].append(
            {
                "id": cid,
                "name": summary["name"],
                **_xy(child),
                "processorCount": summary["processorCount"],
                "outcome": summary["outcome"],
            }
        )
        total += summary["processorCount"]
        worst = _worst([worst, summary["outcome"]])
    entry["processorCount"] = total
    entry["outcome"] = worst
    return gid


_SEVERITY = {
    "": 0,
    "compatible": 0,
    "unknown": 1,
    "config_change": 2,
    "renamed": 2,
    "replaced": 2,
    "deprecated": 2,
    "removed": 3,
    "manual_review": 3,
}


def _worst(outcomes: Any) -> str:
    """The most severe outcome in a group, so a closed group still shows trouble."""
    return max(outcomes, key=lambda o: _SEVERITY.get(o, 1), default="")


def _endpoint(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "id": str(raw.get("id") or ""),
        "groupId": str(raw.get("groupId") or ""),
        "type": str(raw.get("type") or ""),
    }


def _xy(node: dict[str, Any]) -> dict[str, float]:
    pos = node.get("position") if isinstance(node.get("position"), dict) else {}
    return {"x": _num(pos.get("x"), 0), "y": _num(pos.get("y"), 0)}


def _num(value: Any, default: float) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return float(default)
