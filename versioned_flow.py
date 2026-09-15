"""Build a NiFi flow definition (`VersionedFlowSnapshot`) from a 1.x XML template.

NiFi 2.0 removed templates, so a migrated template has to arrive at the target as
a flow definition — the JSON that "Import from JSON" / NiFi Registry accepts. That
format is not a loose bag of fields: it is the serialized form of NiFi's
`VersionedProcessGroup` model, and the importer rejects the whole file if a
required field is missing or an unrecognised one is present. A missing
`componentType`, a connection whose endpoints are not `ConnectableComponent`
objects, or one stray top-level key all surface in the UI as the same unhelpful
"An unexpected error has occurred."

This module therefore writes the shape deliberately and completely, rather than
approximating it:

  * every component carries `identifier`, `groupIdentifier`, `componentType`,
    and `position`
  * connections carry `source` / `destination` objects, not flat id fields
  * the process-group hierarchy is reproduced rather than flattened, so
    connections keep resolving against components in their own group
  * nothing outside the model's vocabulary is emitted

Migration decisions stay in `migration_engine`; this module only serializes. The
caller passes an `apply` callback that is handed each finished component node so
it can rewrite properties or attach manual-review markers.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable

#: Signature of the callback that applies migration decisions to a component.
#: (kind, identifier, node) -> None, mutating `node` in place.
ApplyFn = Callable[[str, str, dict[str, Any]], None]

_DEFAULT_POSITION = {"x": 0.0, "y": 0.0}


def snapshot_from_xml_template(
    root: ET.Element,
    target_version: str,
    apply: ApplyFn | None = None,
    comments: str = "",
) -> dict[str, Any]:
    """Convert a parsed `<template>` into a NiFi flow definition."""
    snippet = root.find("snippet")
    if snippet is None:
        snippet = root

    flow_name = _text(root, "name") or "migrated-flow"
    root_id = _text(root, "groupId") or _new_id()

    # A template whose snippet holds exactly one process group is the common
    # case ("download template" on a group). Promote that group to the root so
    # the import does not nest the whole flow one level deeper than the author
    # drew it.
    groups = snippet.findall("processGroups")
    lone_group = groups[0] if len(groups) == 1 and not _has_direct_components(snippet) else None

    if lone_group is not None:
        contents = _group_to_versioned(lone_group, target_version, apply, root_id)
        contents["identifier"] = root_id
        contents["name"] = _text(lone_group, "name") or flow_name
    else:
        contents = _contents_to_versioned(snippet, target_version, apply, root_id, root_id)
        contents.update(
            {
                "identifier": root_id,
                "instanceIdentifier": _new_id(),
                "name": flow_name,
                "componentType": "PROCESS_GROUP",
                "position": dict(_DEFAULT_POSITION),
                "flowFileConcurrency": "UNBOUNDED",
                "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
            }
        )

    if comments:
        existing = contents.get("comments") or ""
        contents["comments"] = f"{comments}\n\n{existing}".strip()

    return {
        "flowContents": contents,
        "externalControllerServices": {},
        "parameterContexts": {},
        "parameterProviders": {},
        "flowEncodingVersion": "1.0",
        "latest": False,
    }


def _has_direct_components(node: ET.Element) -> bool:
    return any(
        node.find(tag) is not None
        for tag in ("processors", "connections", "controllerServices", "funnels", "inputPorts", "outputPorts")
    )


def _group_to_versioned(
    group: ET.Element,
    target_version: str,
    apply: ApplyFn | None,
    parent_id: str,
) -> dict[str, Any]:
    group_id = _text(group, "id") or _new_id()
    contents = group.find("contents")
    node = _contents_to_versioned(
        contents if contents is not None else ET.Element("contents"),
        target_version,
        apply,
        group_id,
        parent_id,
    )
    node.update(
        {
            "identifier": group_id,
            "instanceIdentifier": _new_id(),
            "name": _text(group, "name") or "group",
            "comments": _text(group, "comments"),
            "componentType": "PROCESS_GROUP",
            "groupIdentifier": parent_id,
            "position": _position(group),
            "flowFileConcurrency": _text(group, "flowfileConcurrency") or "UNBOUNDED",
            "flowFileOutboundPolicy": _text(group, "flowfileOutboundPolicy")
            or "STREAM_WHEN_AVAILABLE",
            "defaultFlowFileExpiration": _text(group, "defaultFlowFileExpiration") or "0 sec",
            "defaultBackPressureObjectThreshold": _int(
                _text(group, "defaultBackPressureObjectThreshold"), 10000
            ),
            "defaultBackPressureDataSizeThreshold": _text(
                group, "defaultBackPressureDataSizeThreshold"
            )
            or "1 GB",
        }
    )
    return node


def _contents_to_versioned(
    contents: ET.Element,
    target_version: str,
    apply: ApplyFn | None,
    group_id: str,
    parent_id: str,
) -> dict[str, Any]:
    processors = []
    for proc in contents.findall("processors"):
        node = _processor_to_versioned(proc, target_version, group_id)
        if apply:
            apply("processor", node["identifier"], node)
        processors.append(node)

    services = []
    for svc in contents.findall("controllerServices"):
        node = _service_to_versioned(svc, target_version, group_id)
        if apply:
            apply("controllerService", node["identifier"], node)
        services.append(node)

    connections = [_connection_to_versioned(c, group_id) for c in contents.findall("connections")]
    input_ports = [_port_to_versioned(p, group_id, "INPUT_PORT") for p in contents.findall("inputPorts")]
    output_ports = [
        _port_to_versioned(p, group_id, "OUTPUT_PORT") for p in contents.findall("outputPorts")
    ]
    funnels = [_funnel_to_versioned(f, group_id) for f in contents.findall("funnels")]
    labels = [_label_to_versioned(l, group_id) for l in contents.findall("labels")]

    child_groups = [
        _group_to_versioned(g, target_version, apply, group_id)
        for g in contents.findall("processGroups")
    ]

    return {
        "processors": processors,
        "controllerServices": services,
        "connections": connections,
        "inputPorts": input_ports,
        "outputPorts": output_ports,
        "funnels": funnels,
        "labels": labels,
        "processGroups": child_groups,
        "remoteProcessGroups": [],
        "variables": {},
    }


def _processor_to_versioned(
    proc: ET.Element, target_version: str, group_id: str
) -> dict[str, Any]:
    config = proc.find("config")
    properties = _properties(config)

    # `<relationships>` carries the authoritative auto-terminate flags; the
    # separate `<autoTerminatedRelationships>` list is not always present.
    relationships = []
    auto_terminated = []
    for rel in proc.findall("relationships"):
        name = _text(rel, "name")
        if not name:
            continue
        auto = _text(rel, "autoTerminate").lower() == "true"
        relationships.append({"name": name, "autoTerminate": auto, "retry": False})
        if auto:
            auto_terminated.append(name)
    if config is not None:
        for rel in config.findall("autoTerminatedRelationships"):
            if rel.text and rel.text not in auto_terminated:
                auto_terminated.append(rel.text)

    return {
        "identifier": _text(proc, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "name": _text(proc, "name"),
        "comments": _text(config, "comments") if config is not None else "",
        "type": _text(proc, "type"),
        "bundle": _bundle(proc, target_version),
        "properties": properties,
        "propertyDescriptors": {},
        "style": _style(proc),
        "schedulingPeriod": _text(config, "schedulingPeriod") or "0 sec",
        "schedulingStrategy": _text(config, "schedulingStrategy") or "TIMER_DRIVEN",
        "executionNode": _text(config, "executionNode") or "ALL",
        "penaltyDuration": _text(config, "penaltyDuration") or "30 sec",
        "yieldDuration": _text(config, "yieldDuration") or "1 sec",
        "bulletinLevel": _text(config, "bulletinLevel") or "WARN",
        "runDurationMillis": _int(_text(config, "runDurationMillis"), 0),
        "concurrentlySchedulableTaskCount": _int(
            _text(config, "concurrentlySchedulableTaskCount"), 1
        ),
        "autoTerminatedRelationships": auto_terminated,
        "relationships": relationships,
        "retryCount": _int(_text(config, "retryCount"), 10),
        "retriedRelationships": [],
        "backoffMechanism": _text(config, "backoffMechanism") or "PENALIZE_FLOWFILE",
        "maxBackoffPeriod": _text(config, "maxBackoffPeriod") or "10 mins",
        "componentType": "PROCESSOR",
        "groupIdentifier": group_id,
        "position": _position(proc),
        # Import the flow stopped: a migrated flow should be reviewed before it
        # starts moving production data.
        "scheduledState": "DISABLED",
    }


def _service_to_versioned(svc: ET.Element, target_version: str, group_id: str) -> dict[str, Any]:
    return {
        "identifier": _text(svc, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "name": _text(svc, "name"),
        "comments": _text(svc, "comments"),
        "type": _text(svc, "type"),
        "bundle": _bundle(svc, target_version),
        "properties": _properties(svc),
        "propertyDescriptors": {},
        "controllerServiceApis": [],
        "componentType": "CONTROLLER_SERVICE",
        "groupIdentifier": group_id,
        "position": _position(svc),
        "scheduledState": "DISABLED",
        "bulletinLevel": _text(svc, "bulletinLevel") or "WARN",
    }


def _connection_to_versioned(conn: ET.Element, group_id: str) -> dict[str, Any]:
    prioritizers = [p.text for p in conn.findall("prioritizers") if p.text]
    bends = []
    for bend in conn.findall("bends"):
        bends.append({"x": _float(_text(bend, "x")), "y": _float(_text(bend, "y"))})

    return {
        "identifier": _text(conn, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "name": _text(conn, "name"),
        "source": _connectable(conn.find("source"), group_id),
        "destination": _connectable(conn.find("destination"), group_id),
        "selectedRelationships": [r.text for r in conn.findall("selectedRelationships") if r.text],
        "backPressureObjectThreshold": _int(_text(conn, "backPressureObjectThreshold"), 10000),
        "backPressureDataSizeThreshold": _text(conn, "backPressureDataSizeThreshold") or "1 GB",
        "flowFileExpiration": _text(conn, "flowFileExpiration") or "0 sec",
        "prioritizers": prioritizers,
        "bends": bends,
        "labelIndex": _int(_text(conn, "labelIndex"), 1),
        "zIndex": _int(_text(conn, "zIndex"), 0),
        "loadBalanceStrategy": _text(conn, "loadBalanceStrategy") or "DO_NOT_LOAD_BALANCE",
        "partitioningAttribute": _text(conn, "loadBalancePartitionAttribute"),
        "loadBalanceCompression": _text(conn, "loadBalanceCompression") or "DO_NOT_COMPRESS",
        "componentType": "CONNECTION",
        "groupIdentifier": group_id,
    }


def _connectable(node: ET.Element | None, group_id: str) -> dict[str, Any]:
    """A `ConnectableComponent`. The importer resolves a connection through this,
    so an empty id here silently detaches the connection."""
    if node is None:
        return {"id": "", "type": "PROCESSOR", "groupId": group_id, "name": ""}
    return {
        "id": _text(node, "id"),
        "type": _text(node, "type") or "PROCESSOR",
        "groupId": _text(node, "groupId") or group_id,
        "name": _text(node, "name"),
        "comments": "",
        "instanceIdentifier": _text(node, "id"),
    }


def _port_to_versioned(port: ET.Element, group_id: str, port_type: str) -> dict[str, Any]:
    return {
        "identifier": _text(port, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "name": _text(port, "name"),
        "comments": _text(port, "comments"),
        "type": port_type,
        "concurrentlySchedulableTaskCount": _int(
            _text(port, "concurrentlySchedulableTaskCount"), 1
        ),
        "allowRemoteAccess": _text(port, "allowRemoteAccess").lower() == "true",
        "componentType": port_type,
        "groupIdentifier": group_id,
        "position": _position(port),
        "scheduledState": "DISABLED",
    }


def _funnel_to_versioned(funnel: ET.Element, group_id: str) -> dict[str, Any]:
    return {
        "identifier": _text(funnel, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "componentType": "FUNNEL",
        "groupIdentifier": group_id,
        "position": _position(funnel),
    }


def _label_to_versioned(label: ET.Element, group_id: str) -> dict[str, Any]:
    return {
        "identifier": _text(label, "id") or _new_id(),
        "instanceIdentifier": _new_id(),
        "label": _text(label, "label"),
        "width": _float(_text(label, "width")) or 150.0,
        "height": _float(_text(label, "height")) or 50.0,
        "style": _style(label),
        "zIndex": _int(_text(label, "zIndex"), 0),
        "componentType": "LABEL",
        "groupIdentifier": group_id,
        "position": _position(label),
    }


# --- Small helpers ------------------------------------------------------------


def _text(node: ET.Element | None, child: str) -> str:
    if node is None:
        return ""
    found = node.find(child)
    return (found.text or "").strip() if found is not None and found.text else ""


def _properties(node: ET.Element | None) -> dict[str, Any]:
    """Template properties are `<entry><key/><value/></entry>` pairs.

    An entry with no `<value>` means the property is unset; it is emitted as
    JSON null rather than an empty string, because "" is a real value for some
    NiFi properties and would change behaviour.
    """
    out: dict[str, Any] = {}
    if node is None:
        return out
    for entry in node.findall("properties/entry"):
        key = _text(entry, "key")
        if not key:
            continue
        value_node = entry.find("value")
        out[key] = value_node.text if value_node is not None and value_node.text is not None else None
    return out


def _bundle(node: ET.Element, target_version: str) -> dict[str, str]:
    bundle = node.find("bundle")
    if bundle is None:
        return {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": target_version}
    return {
        "group": _text(bundle, "group") or "org.apache.nifi",
        "artifact": _text(bundle, "artifact") or "nifi-standard-nar",
        # The NAR version follows the NiFi release, so it must be re-stamped.
        "version": target_version,
    }


def _position(node: ET.Element) -> dict[str, float]:
    pos = node.find("position")
    if pos is None:
        return dict(_DEFAULT_POSITION)
    return {"x": _float(_text(pos, "x")), "y": _float(_text(pos, "y"))}


def _style(node: ET.Element) -> dict[str, str]:
    style = node.find("style")
    out: dict[str, str] = {}
    if style is None:
        return out
    for entry in style.findall("entry"):
        key = _text(entry, "key")
        if key:
            out[key] = _text(entry, "value")
    return out


def _int(value: str, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _new_id() -> str:
    return str(uuid.uuid4())
