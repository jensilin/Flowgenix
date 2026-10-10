"""Import readiness: things NiFi will complain about after the file imports.

Version compatibility is the migration engine's job. This pass looks at the
flow itself and lists what an operator has to do on the target before the
flow can run: credentials that were never exported, absolute paths, invalid
processors and dangling references. It reads the source; nothing is changed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from flow_document import FlowDocument
from flow_graph import (
    component_id,
    declared_relationships,
    items,
    root_group,
    source_snapshot,
    walk_groups,
)

#: Blocks a clean start: NiFi marks the component invalid.
SEVERITY_ACTION = "action"
#: Worth confirming, but the flow can run as is.
SEVERITY_CHECK = "check"

_PATH_KEY = re.compile(r"director|path|file|folder|\bdir\b|location", re.I)
_ABSOLUTE = re.compile(r"^(?:/[^/\s]|[A-Za-z]:[\\/]|\\\\[^\\\s])")


def check_readiness(doc: FlowDocument) -> dict[str, Any]:
    snapshot = source_snapshot(doc)
    groups = list(walk_groups(root_group(snapshot)))
    declared = declared_relationships(doc)

    service_ids = {
        component_id(svc) for group, _ in groups for svc in items(group, "controllerServices")
    }
    external = snapshot.get("externalControllerServices")
    external_names: dict[str, str] = {}
    if isinstance(external, dict):
        for key, ref in external.items():
            if isinstance(ref, dict):
                external_names[str(ref.get("identifier") or key)] = str(ref.get("name") or "")

    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, int] = defaultdict(int)
    for group, _ in groups:
        for conn in items(group, "connections"):
            source = str((conn.get("source") or {}).get("id") or "")
            destination = str((conn.get("destination") or {}).get("id") or "")
            outgoing[source].update(str(r) for r in conn.get("selectedRelationships") or [])
            outgoing[source].add("")  # marks "has at least one outgoing connection"
            incoming[destination] += 1

    found: list[dict[str, Any]] = []
    for group, path in groups:
        configurable = [("processor", p) for p in items(group, "processors")]
        configurable += [("controller service", s) for s in items(group, "controllerServices")]
        for kind, node in configurable:
            found += _sensitive(kind, node, path)
            found += _paths(kind, node, path)
            found += _services(kind, node, path, service_ids, external_names)
        for proc in items(group, "processors"):
            found += _relationships(proc, path, declared, outgoing)
        for funnel in items(group, "funnels"):
            ident = component_id(funnel)
            if "" not in outgoing.get(ident, set()):
                found.append(
                    _item(
                        "funnel",
                        SEVERITY_CHECK,
                        "No outgoing connection",
                        f"Funnel {ident[:8]}",
                        ident,
                        path,
                        f"{incoming.get(ident, 0)} incoming, 0 outgoing",
                        "FlowFiles that reach it queue forever and NiFi flags it invalid. "
                        "Connect it onward or delete it.",
                    )
                )

    found.sort(key=lambda item: (item["severity"] != SEVERITY_ACTION, item["groupPath"], item["component"]))
    counts = {
        SEVERITY_ACTION: sum(1 for i in found if i["severity"] == SEVERITY_ACTION),
        SEVERITY_CHECK: sum(1 for i in found if i["severity"] == SEVERITY_CHECK),
    }
    return {
        "items": found,
        "counts": counts,
        "relationshipsChecked": bool(declared),
    }


def _sensitive(kind: str, node: dict[str, Any], path: str) -> list[dict[str, Any]]:
    props = node.get("properties") or {}
    descriptors = node.get("propertyDescriptors") or {}
    empty = [
        str(desc.get("displayName") or key)
        for key, desc in descriptors.items()
        if isinstance(desc, dict) and desc.get("sensitive") and props.get(key) in (None, "")
    ]
    if not empty:
        return []
    return [
        _item(
            "sensitive",
            SEVERITY_CHECK,
            "Sensitive values were not exported",
            node.get("name") or kind,
            component_id(node),
            path,
            ", ".join(empty),
            "NiFi never exports passwords or keys. Re-enter any of these that were set, "
            "ideally as sensitive parameters.",
        )
    ]


def _paths(kind: str, node: dict[str, Any], path: str) -> list[dict[str, Any]]:
    hits = [
        f"{key} = {value}"
        for key, value in (node.get("properties") or {}).items()
        if isinstance(value, str) and _PATH_KEY.search(key) and _ABSOLUTE.match(value.strip())
    ]
    if not hits:
        return []
    return [
        _item(
            "path",
            SEVERITY_CHECK,
            "Absolute file system path",
            node.get("name") or kind,
            component_id(node),
            path,
            "; ".join(hits),
            "The path has to exist, with the same permissions, on every target node. "
            "Consider a parameter so each environment can set its own.",
        )
    ]


def _services(
    kind: str,
    node: dict[str, Any],
    path: str,
    service_ids: set[str],
    external_names: dict[str, str],
) -> list[dict[str, Any]]:
    props = node.get("properties") or {}
    out = []
    for key, desc in (node.get("propertyDescriptors") or {}).items():
        if not isinstance(desc, dict) or not desc.get("identifiesControllerService"):
            continue
        ref = props.get(key)
        if not isinstance(ref, str) or not ref.strip() or ref.startswith("#{") or ref in service_ids:
            continue
        label = str(desc.get("displayName") or key)
        if ref in external_names:
            out.append(
                _item(
                    "service",
                    SEVERITY_CHECK,
                    "Uses a controller service from outside the flow",
                    node.get("name") or kind,
                    component_id(node),
                    path,
                    f"{label} → {external_names[ref] or ref}",
                    "NiFi matches it by name on import. Make sure a service with that name "
                    "exists above the imported group, or select another one.",
                )
            )
        else:
            out.append(
                _item(
                    "service",
                    SEVERITY_ACTION,
                    "Controller service is not in this file",
                    node.get("name") or kind,
                    component_id(node),
                    path,
                    f"{label} → {ref}",
                    "The referenced service was defined outside the exported group. Create it "
                    "on the target and select it in this property.",
                )
            )
    return out


def _relationships(
    proc: dict[str, Any],
    path: str,
    declared: dict[str, list[str]],
    outgoing: dict[str, set[str]],
) -> list[dict[str, Any]]:
    ident = component_id(proc)
    offered = declared.get(ident)
    if not offered:
        return []
    handled = set(proc.get("autoTerminatedRelationships") or []) | outgoing.get(ident, set())
    unhandled = [rel for rel in offered if rel not in handled]
    if not unhandled:
        return []
    return [
        _item(
            "relationship",
            SEVERITY_ACTION,
            "Relationship is neither connected nor auto-terminated",
            proc.get("name") or "processor",
            ident,
            path,
            ", ".join(unhandled),
            "NiFi will not start the processor until every relationship is connected or "
            "auto-terminated.",
        )
    ]


def _item(
    kind: str,
    severity: str,
    title: str,
    component: str,
    identifier: str,
    group_path: str,
    detail: str,
    fix: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "component": str(component),
        "componentId": identifier,
        "groupPath": group_path,
        "detail": detail,
        "fix": fix,
    }
