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

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from flow_schema import (
    NIFI_BUNDLE_GROUP,
    follows_nifi_release,
    xml_children,
    xml_encoding_attribute,
    xml_encoding_for_version,
)
from nifi_versions import parse_version
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
    root_id = _text(root, "groupId") or _id_for(snippet)

    # A template whose snippet holds exactly one process group is the common
    # case ("download template" on a group). Promote that group to the root so
    # the import does not nest the whole flow one level deeper than the author
    # drew it.
    #
    # Keep the group's own identifier. `<groupId>` on the template is the
    # *parent canvas* the template was downloaded from — rewriting the root to
    # that id leaves every child pointing at the original group, so NiFi opens
    # an empty or disconnected canvas.
    groups = xml_children(snippet, "processGroups")
    lone_group = groups[0] if len(groups) == 1 and not _has_direct_components(snippet) else None

    if lone_group is not None:
        contents = _group_to_versioned(lone_group, target_version, apply, "")
        if not contents.get("name"):
            contents["name"] = flow_name
    else:
        contents = _contents_to_versioned(snippet, target_version, apply, root_id, root_id)
        contents.update(
            {
                "identifier": root_id,
                "instanceIdentifier": root_id,
                "name": flow_name,
                "componentType": "PROCESS_GROUP",
                "position": dict(_DEFAULT_POSITION),
                "flowFileConcurrency": "UNBOUNDED",
                "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
            }
        )

    # The root of a flow definition has no parent.
    contents.pop("groupIdentifier", None)

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
        xml_children(node, tag)
        for tag in (
            "processors", "connections", "controllerServices", "funnels", "inputPorts",
            "outputPorts", "labels", "remoteProcessGroups",
        )
    )


def _group_to_versioned(
    group: ET.Element,
    target_version: str,
    apply: ApplyFn | None,
    parent_id: str,
) -> dict[str, Any]:
    group_id = _text(group, "id") or _id_for(group)
    contents = group.find("contents")
    source = contents if contents is not None else group
    node = _contents_to_versioned(
        source,
        target_version,
        apply,
        group_id,
        parent_id,
    )
    node.update(
        {
            "identifier": group_id,
            "instanceIdentifier": group_id,
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
    group_vars = _variables(group)
    if group_vars:
        node["variables"] = group_vars
    return node


def _contents_to_versioned(
    contents: ET.Element,
    target_version: str,
    apply: ApplyFn | None,
    group_id: str,
    parent_id: str,
) -> dict[str, Any]:
    processors = []
    for proc in xml_children(contents, "processors"):
        node = _processor_to_versioned(proc, target_version, group_id)
        if apply:
            apply("processor", node["identifier"], node)
        processors.append(node)

    services = []
    for svc in xml_children(contents, "controllerServices"):
        node = _service_to_versioned(svc, target_version, group_id)
        if apply:
            apply("controllerService", node["identifier"], node)
        services.append(node)

    connections = [
        _connection_to_versioned(c, group_id) for c in xml_children(contents, "connections")
    ]
    input_ports = [
        _port_to_versioned(p, group_id, "INPUT_PORT") for p in xml_children(contents, "inputPorts")
    ]
    output_ports = [
        _port_to_versioned(p, group_id, "OUTPUT_PORT") for p in xml_children(contents, "outputPorts")
    ]
    funnels = [_funnel_to_versioned(f, group_id) for f in xml_children(contents, "funnels")]
    labels = [_label_to_versioned(l, group_id) for l in xml_children(contents, "labels")]
    remote_groups = [
        _rpg_to_versioned(r, group_id) for r in xml_children(contents, "remoteProcessGroups")
    ]

    child_groups = [
        _group_to_versioned(g, target_version, apply, group_id)
        for g in xml_children(contents, "processGroups")
    ]

    _fill_connectable_names(
        connections, processors + services + funnels + input_ports + output_ports
    )

    return {
        "processors": processors,
        "controllerServices": services,
        "connections": connections,
        "inputPorts": input_ports,
        "outputPorts": output_ports,
        "funnels": funnels,
        "labels": labels,
        "processGroups": child_groups,
        "remoteProcessGroups": remote_groups,
        "variables": _variables(contents),
    }


def _processor_to_versioned(
    proc: ET.Element, target_version: str, group_id: str
) -> dict[str, Any]:
    config = proc.find("config")
    properties = _properties(config)
    identifier = _text(proc, "id") or _id_for(proc)

    # A template's `<relationships>` block carries the auto-terminate and retry
    # flags per relationship. `VersionedProcessor` has no `relationships` field —
    # it splits the same information across two name sets.
    auto_terminated: list[str] = []
    retried: list[str] = []
    for rel in proc.findall("relationships"):
        name = _text(rel, "name")
        if not name:
            continue
        if _text(rel, "autoTerminate").lower() == "true":
            auto_terminated.append(name)
        if _text(rel, "retry").lower() == "true":
            retried.append(name)
    if config is not None:
        for rel in config.findall("autoTerminatedRelationships"):
            if rel.text and rel.text not in auto_terminated:
                auto_terminated.append(rel.text)

    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "name": _text(proc, "name"),
        "comments": _text(config, "comments") if config is not None else "",
        "type": _text(proc, "type"),
        "bundle": _bundle(proc, target_version),
        "properties": properties,
        "propertyDescriptors": _property_descriptors(config),
        # UpdateAttribute's rule engine and several scripted processors keep
        # their real configuration here, so dropping it silently guts the flow.
        "annotationData": _text(config, "annotationData") if config is not None else "",
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
        "retryCount": _int(_text(config, "retryCount"), 10),
        "retriedRelationships": retried,
        "backoffMechanism": _text(config, "backoffMechanism") or "PENALIZE_FLOWFILE",
        "maxBackoffPeriod": _text(config, "maxBackoffPeriod") or "10 mins",
        "componentType": "PROCESSOR",
        "groupIdentifier": group_id,
        "position": _position(proc),
        # Versioned snapshots have ENABLED/DISABLED only. STOPPED/RUNNING in a
        # template means "allowed to run" — NiFi still imports the flow unstarted.
        # Forcing DISABLED greys out every processor and is not how the canvas
        # was drawn.
        "scheduledState": _scheduled_state(_text(proc, "state")),
    }


def _service_to_versioned(svc: ET.Element, target_version: str, group_id: str) -> dict[str, Any]:
    identifier = _text(svc, "id") or _id_for(svc)
    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "name": _text(svc, "name"),
        "comments": _text(svc, "comments"),
        "type": _text(svc, "type"),
        "bundle": _bundle(svc, target_version),
        "properties": _properties(svc),
        "propertyDescriptors": _property_descriptors(svc),
        "annotationData": _text(svc, "annotationData"),
        "controllerServiceApis": [],
        "componentType": "CONTROLLER_SERVICE",
        "groupIdentifier": group_id,
        "position": _position(svc),
        "scheduledState": _scheduled_state(_text(svc, "state")),
        "bulletinLevel": _text(svc, "bulletinLevel") or "WARN",
    }


def _connection_to_versioned(conn: ET.Element, group_id: str) -> dict[str, Any]:
    prioritizers = [p.text for p in conn.findall("prioritizers") if p.text]
    bends = []
    for bend in conn.findall("bends"):
        bends.append({"x": _float(_text(bend, "x")), "y": _float(_text(bend, "y"))})

    identifier = _text(conn, "id") or _id_for(conn)
    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
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
    identifier = _text(port, "id") or _id_for(port)
    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
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
        "scheduledState": _scheduled_state(_text(port, "state")),
    }


def _funnel_to_versioned(funnel: ET.Element, group_id: str) -> dict[str, Any]:
    identifier = _text(funnel, "id") or _id_for(funnel)
    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "componentType": "FUNNEL",
        "groupIdentifier": group_id,
        "position": _position(funnel),
    }


def _label_to_versioned(label: ET.Element, group_id: str) -> dict[str, Any]:
    identifier = _text(label, "id") or _id_for(label)
    return {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "label": _text(label, "label"),
        "width": _float(_text(label, "width")) or 150.0,
        "height": _float(_text(label, "height")) or 50.0,
        "style": _style(label),
        "zIndex": _int(_text(label, "zIndex"), 0),
        "componentType": "LABEL",
        "groupIdentifier": group_id,
        "position": _position(label),
    }


def _rpg_to_versioned(rpg: ET.Element, group_id: str) -> dict[str, Any]:
    identifier = _text(rpg, "id") or _id_for(rpg)
    contents = rpg.find("contents")
    target_uris = _text(rpg, "targetUris") or _text(rpg, "targetUri")
    node: dict[str, Any] = {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "name": _text(rpg, "name") or target_uris,
        "comments": _text(rpg, "comments"),
        "targetUri": _text(rpg, "targetUri") or target_uris.split(",")[0].strip(),
        "targetUris": target_uris,
        "communicationsTimeout": _text(rpg, "communicationsTimeout") or "30 sec",
        "yieldDuration": _text(rpg, "yieldDuration") or "10 sec",
        "transportProtocol": _text(rpg, "transportProtocol") or "RAW",
        "inputPorts": [
            _remote_port_to_versioned(p, identifier, "REMOTE_INPUT_PORT")
            for p in (xml_children(contents, "inputPorts") if contents is not None else [])
        ],
        "outputPorts": [
            _remote_port_to_versioned(p, identifier, "REMOTE_OUTPUT_PORT")
            for p in (xml_children(contents, "outputPorts") if contents is not None else [])
        ],
        "componentType": "REMOTE_PROCESS_GROUP",
        "groupIdentifier": group_id,
        "position": _position(rpg),
    }
    for key in ("localNetworkInterface", "proxyHost", "proxyUser"):
        if _text(rpg, key):
            node[key] = _text(rpg, key)
    if _text(rpg, "proxyPort"):
        node["proxyPort"] = _int(_text(rpg, "proxyPort"), 0)
    return node


def _remote_port_to_versioned(port: ET.Element, rpg_id: str, port_type: str) -> dict[str, Any]:
    identifier = _text(port, "id") or _id_for(port)
    batch = port.find("batchSettings")
    node: dict[str, Any] = {
        "identifier": identifier,
        "instanceIdentifier": identifier,
        "name": _text(port, "name"),
        "comments": _text(port, "comments"),
        "remoteGroupId": rpg_id,
        "targetId": _text(port, "targetId") or identifier,
        "concurrentlySchedulableTaskCount": _int(_text(port, "concurrentlySchedulableTaskCount"), 1),
        "useCompression": _text(port, "useCompression").lower() == "true",
        "componentType": port_type,
        "groupIdentifier": rpg_id,
        "scheduledState": "ENABLED",
    }
    if batch is not None:
        node["batchSize"] = {
            k: v
            for k, v in (
                ("count", _int(_text(batch, "count"), 0) or None),
                ("size", _text(batch, "size") or None),
                ("period", _text(batch, "duration") or None),
            )
            if v is not None
        }
    return node


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
        out[key] = _property_value(entry.find("value"))
    return out


def _property_value(value_node: ET.Element | None) -> Any:
    if value_node is None:
        return None
    raw = value_node.text
    if raw is None:
        return None
    if raw.strip() == "":
        # A lone newline is a real MergeContent demarcator. Indented pretty-print
        # of an empty value is not.
        if raw in ("\n", "\r\n", "\r"):
            return "\n"
        return None
    return raw


def _property_descriptors(node: ET.Element | None) -> dict[str, Any]:
    """Copy template `<descriptors>` into the VersionedPropertyDescriptor map.

    Only the fields that model has are emitted. A template descriptor also
    carries `<dependencies>`, which NiFi recomputes from the processor's own
    definition; copying it into the flow definition adds a field the importer
    does not know.
    """
    out: dict[str, Any] = {}
    if node is None:
        return out
    descriptors = node.find("descriptors")
    if descriptors is None:
        return out
    for entry in descriptors.findall("entry"):
        key = _text(entry, "key")
        if not key:
            continue
        value = entry.find("value")
        name = _text(value, "name") or key
        # A template stores the service API class name here, not a boolean.
        service_api = _text(value, "identifiesControllerService")
        out[key] = {
            "name": name,
            "displayName": _text(value, "displayName") or name,
            "identifiesControllerService": service_api.lower() not in ("", "false"),
            "sensitive": _text(value, "sensitive").lower() == "true",
            "dynamic": _text(value, "dynamic").lower() == "true",
        }
    return out


def _variables(node: ET.Element | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if node is None:
        return out
    for var in xml_children(node, "variables"):
        name = _text(var, "name")
        if name:
            out[name] = _text(var, "value")
    return out


def _fill_connectable_names(connections: list[dict[str, Any]], components: list[dict[str, Any]]) -> None:
    names = {
        str(node.get("identifier")): str(node.get("name") or "")
        for node in components
        if node.get("identifier")
    }
    for conn in connections:
        for end in ("source", "destination"):
            endpoint = conn.get(end)
            if not isinstance(endpoint, dict):
                continue
            if not endpoint.get("name"):
                endpoint["name"] = names.get(str(endpoint.get("id") or ""), "")


def _scheduled_state(raw: str) -> str:
    """Map template `<state>` onto a VersionedComponent scheduledState."""
    state = (raw or "").strip().upper()
    if state == "DISABLED":
        return "DISABLED"
    return "ENABLED"


def _xml_state(scheduled_state: Any) -> str:
    state = str(scheduled_state or "").strip().upper()
    if state == "DISABLED":
        return "DISABLED"
    return "STOPPED"


def _bundle(node: ET.Element, target_version: str) -> dict[str, str]:
    bundle = node.find("bundle")
    if bundle is None:
        return {"group": NIFI_BUNDLE_GROUP, "artifact": "nifi-standard-nar", "version": target_version}
    out = {
        "group": _text(bundle, "group") or NIFI_BUNDLE_GROUP,
        "artifact": _text(bundle, "artifact") or "nifi-standard-nar",
        "version": _text(bundle, "version") or target_version,
    }
    if follows_nifi_release(out):
        out["version"] = target_version
    return out


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


_ID_NAMESPACE = uuid.UUID("6f1c2a52-6c55-4d7e-9a1f-0f5c2b8e9d41")


def _id_for(element: ET.Element) -> str:
    """A stable id for an element that has none, so the same file always
    produces the same output (and re-imports keep matching components)."""
    return str(uuid.uuid5(_ID_NAMESPACE, hashlib.sha256(ET.tostring(element)).hexdigest()))


def _new_id(node: Any = None) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, json.dumps(node, sort_keys=True, default=str)))


# --- JSON snapshot → XML template --------------------------------------------


def template_from_snapshot(snapshot: dict[str, Any], target_version: str) -> str:
    """Serialize a VersionedFlowSnapshot as a 1.x XML template.

    Emits the *target* encoding's tags and `encoding-version` attribute, not
    whatever the source file happened to use.
    """
    root_group = snapshot.get("flowContents") or snapshot.get("rootGroup") or {}
    if not isinstance(root_group, dict):
        raise ValueError("JSON flow definition has no flowContents to convert to XML.")

    encoding = xml_encoding_for_version(target_version)
    template = ET.Element("template")
    template.set(xml_encoding_attribute(), encoding)
    _set_text(template, "description", str(root_group.get("comments") or "Migrated by Flowgenix"))
    _set_text(template, "name", str(root_group.get("name") or "migrated-flow"))
    _set_text(template, "groupId", str(root_group.get("identifier") or _new_id(root_group)))

    snippet = ET.SubElement(template, "snippet")
    snippet.append(_group_to_xml(root_group, target_version))

    ET.indent(template, space="  ")
    xml_body = ET.tostring(template, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml_body + "\n"


def _group_to_xml(group: dict[str, Any], target_version: str) -> ET.Element:
    node = ET.Element("processGroups")
    _set_text(node, "id", str(group.get("identifier") or _new_id(group)))
    _set_text(node, "name", str(group.get("name") or "group"))
    comments = str(group.get("comments") or "")
    if comments:
        _set_text(node, "comments", comments)
    _position_xml(node, group.get("position"))
    _set_text(node, "flowfileConcurrency", str(group.get("flowFileConcurrency") or "UNBOUNDED"))
    _set_text(
        node, "flowfileOutboundPolicy", str(group.get("flowFileOutboundPolicy") or "STREAM_WHEN_AVAILABLE")
    )
    for key, default in (
        ("defaultFlowFileExpiration", "0 sec"),
        ("defaultBackPressureObjectThreshold", 10000),
        ("defaultBackPressureDataSizeThreshold", "1 GB"),
    ):
        value = group.get(key)
        if value not in (None, "") and str(value) != str(default):
            _set_text(node, key, str(value))

    contents = ET.SubElement(node, "contents")
    for proc in group.get("processors") or []:
        contents.append(_processor_to_xml(proc, target_version))
    for svc in group.get("controllerServices") or []:
        contents.append(_service_to_xml(svc, target_version))
    for conn in group.get("connections") or []:
        contents.append(_connection_to_xml(conn, target_version))
    for port in group.get("inputPorts") or []:
        contents.append(_port_to_xml(port, "inputPorts"))
    for port in group.get("outputPorts") or []:
        contents.append(_port_to_xml(port, "outputPorts"))
    for funnel in group.get("funnels") or []:
        contents.append(_funnel_to_xml(funnel))
    for label in group.get("labels") or []:
        contents.append(_label_to_xml(label))
    for rpg in group.get("remoteProcessGroups") or []:
        contents.append(_rpg_to_xml(rpg))
    for child in group.get("processGroups") or []:
        contents.append(_group_to_xml(child, target_version))

    variables = group.get("variables")
    if isinstance(variables, dict):
        for name, value in variables.items():
            var = ET.SubElement(node, "variables")
            _set_text(var, "name", str(name))
            _set_text(var, "value", "" if value is None else str(value))
    return node


def _processor_to_xml(proc: dict[str, Any], target_version: str) -> ET.Element:
    node = ET.Element("processors")
    _set_text(node, "id", str(proc.get("identifier") or proc.get("id") or _new_id(proc)))
    _set_text(node, "name", str(proc.get("name") or ""))
    _set_text(node, "type", str(proc.get("type") or ""))
    _bundle_xml(node, proc.get("bundle"), target_version)
    _position_xml(node, proc.get("position"))
    _style_xml(node, proc.get("style"))
    config = ET.SubElement(node, "config")
    _set_text(config, "schedulingStrategy", str(proc.get("schedulingStrategy") or "TIMER_DRIVEN"))
    _set_text(config, "schedulingPeriod", str(proc.get("schedulingPeriod") or "0 sec"))
    _set_text(config, "executionNode", str(proc.get("executionNode") or "ALL"))
    _set_text(config, "penaltyDuration", str(proc.get("penaltyDuration") or "30 sec"))
    _set_text(config, "yieldDuration", str(proc.get("yieldDuration") or "1 sec"))
    _set_text(config, "bulletinLevel", str(proc.get("bulletinLevel") or "WARN"))
    _set_text(
        config,
        "concurrentlySchedulableTaskCount",
        str(_int(str(proc.get("concurrentlySchedulableTaskCount") or 1), 1)),
    )
    _set_text(config, "runDurationMillis", str(_int(str(proc.get("runDurationMillis") or 0), 0)))
    # Per-relationship retry arrived in 1.16; older templates have no such fields.
    if parse_version(target_version) >= (1, 16, 0):
        if proc.get("retryCount") is not None:
            _set_text(config, "retryCount", str(_int(str(proc.get("retryCount")), 10)))
        if proc.get("backoffMechanism"):
            _set_text(config, "backoffMechanism", str(proc.get("backoffMechanism")))
        if proc.get("maxBackoffPeriod"):
            _set_text(config, "maxBackoffPeriod", str(proc.get("maxBackoffPeriod")))
    comments = str(proc.get("comments") or "")
    if comments:
        _set_text(config, "comments", comments)
    annotation = str(proc.get("annotationData") or "")
    if annotation:
        _set_text(config, "annotationData", annotation)
    _descriptors_xml(config, proc.get("propertyDescriptors"))
    _properties_xml(config, proc.get("properties"))

    # A template lists every relationship once, with its flags; the flow
    # definition splits them into two name sets. Rebuild the template form.
    auto = [str(r) for r in proc.get("autoTerminatedRelationships") or []]
    retried = [str(r) for r in proc.get("retriedRelationships") or []]
    for rel in auto:
        _set_text(config, "autoTerminatedRelationships", rel)
    for name in auto + [r for r in retried if r not in auto]:
        rel_el = ET.SubElement(node, "relationships")
        _set_text(rel_el, "name", name)
        _set_text(rel_el, "autoTerminate", "true" if name in auto else "false")
        _set_text(rel_el, "retry", "true" if name in retried else "false")
    _set_text(node, "state", _xml_state(proc.get("scheduledState")))
    return node


def _service_to_xml(svc: dict[str, Any], target_version: str) -> ET.Element:
    node = ET.Element("controllerServices")
    _set_text(node, "id", str(svc.get("identifier") or svc.get("id") or _new_id(svc)))
    _set_text(node, "name", str(svc.get("name") or ""))
    _set_text(node, "type", str(svc.get("type") or ""))
    comments = str(svc.get("comments") or "")
    if comments:
        _set_text(node, "comments", comments)
    annotation = str(svc.get("annotationData") or "")
    if annotation:
        _set_text(node, "annotationData", annotation)
    if svc.get("bulletinLevel"):
        _set_text(node, "bulletinLevel", str(svc.get("bulletinLevel")))
    _bundle_xml(node, svc.get("bundle"), target_version)
    _descriptors_xml(node, svc.get("propertyDescriptors"))
    _properties_xml(node, svc.get("properties"))
    return node


def _connection_to_xml(conn: dict[str, Any], target_version: str) -> ET.Element:
    node = ET.Element("connections")
    _set_text(node, "id", str(conn.get("identifier") or conn.get("id") or _new_id(conn)))
    if conn.get("name"):
        _set_text(node, "name", str(conn.get("name")))
    _connectable_xml(node, "source", conn.get("source"))
    _connectable_xml(node, "destination", conn.get("destination"))
    for rel in conn.get("selectedRelationships") or []:
        _set_text(node, "selectedRelationships", str(rel))
    _set_text(
        node,
        "backPressureObjectThreshold",
        str(conn.get("backPressureObjectThreshold") or 10000),
    )
    _set_text(
        node,
        "backPressureDataSizeThreshold",
        str(conn.get("backPressureDataSizeThreshold") or "1 GB"),
    )
    _set_text(node, "flowFileExpiration", str(conn.get("flowFileExpiration") or "0 sec"))
    for prioritizer in conn.get("prioritizers") or []:
        _set_text(node, "prioritizers", str(prioritizer))
    for bend in conn.get("bends") or []:
        if isinstance(bend, dict):
            bend_el = ET.SubElement(node, "bends")
            _set_text(bend_el, "x", str(float(bend.get("x") or 0)))
            _set_text(bend_el, "y", str(float(bend.get("y") or 0)))
    _set_text(node, "labelIndex", str(_int(str(conn.get("labelIndex", 1)), 1)))
    _set_text(node, "zIndex", str(_int(str(conn.get("zIndex", 0)), 0)))
    # Cluster load balancing arrived in 1.8.
    if parse_version(target_version) >= (1, 8, 0):
        _set_text(node, "loadBalanceStrategy", str(conn.get("loadBalanceStrategy") or "DO_NOT_LOAD_BALANCE"))
        if conn.get("partitioningAttribute"):
            _set_text(node, "loadBalancePartitionAttribute", str(conn.get("partitioningAttribute")))
        _set_text(
            node, "loadBalanceCompression", str(conn.get("loadBalanceCompression") or "DO_NOT_COMPRESS")
        )
    return node


def _connectable_xml(parent: ET.Element, tag: str, data: Any) -> None:
    node = ET.SubElement(parent, tag)
    payload = data if isinstance(data, dict) else {}
    _set_text(node, "id", str(payload.get("id") or ""))
    _set_text(node, "groupId", str(payload.get("groupId") or ""))
    _set_text(node, "type", str(payload.get("type") or "PROCESSOR"))
    if payload.get("name"):
        _set_text(node, "name", str(payload.get("name")))


def _port_to_xml(port: dict[str, Any], tag: str) -> ET.Element:
    node = ET.Element(tag)
    _set_text(node, "id", str(port.get("identifier") or port.get("id") or _new_id(port)))
    _set_text(node, "name", str(port.get("name") or ""))
    _position_xml(node, port.get("position"))
    comments = str(port.get("comments") or "")
    if comments:
        _set_text(node, "comments", comments)
    _set_text(
        node,
        "concurrentlySchedulableTaskCount",
        str(_int(str(port.get("concurrentlySchedulableTaskCount") or 1), 1)),
    )
    if port.get("allowRemoteAccess"):
        _set_text(node, "allowRemoteAccess", "true")
    _set_text(node, "state", _xml_state(port.get("scheduledState")))
    return node


def _funnel_to_xml(funnel: dict[str, Any]) -> ET.Element:
    node = ET.Element("funnels")
    _set_text(node, "id", str(funnel.get("identifier") or funnel.get("id") or _new_id(funnel)))
    _position_xml(node, funnel.get("position"))
    return node


def _label_to_xml(label: dict[str, Any]) -> ET.Element:
    node = ET.Element("labels")
    _set_text(node, "id", str(label.get("identifier") or label.get("id") or _new_id(label)))
    _set_text(node, "label", str(label.get("label") or label.get("name") or ""))
    _position_xml(node, label.get("position"))
    _set_text(node, "width", str(float(label.get("width") or 150.0)))
    _set_text(node, "height", str(float(label.get("height") or 50.0)))
    _style_xml(node, label.get("style"))
    if label.get("zIndex"):
        _set_text(node, "zIndex", str(_int(str(label.get("zIndex")), 0)))
    return node


def _rpg_to_xml(rpg: dict[str, Any]) -> ET.Element:
    node = ET.Element("remoteProcessGroups")
    identifier = str(rpg.get("identifier") or rpg.get("id") or _new_id(rpg))
    _set_text(node, "id", identifier)
    _position_xml(node, rpg.get("position"))
    comments = str(rpg.get("comments") or "")
    if comments:
        _set_text(node, "comments", comments)
    _set_text(node, "communicationsTimeout", str(rpg.get("communicationsTimeout") or "30 sec"))
    contents = ET.SubElement(node, "contents")
    for key, tag in (("inputPorts", "inputPorts"), ("outputPorts", "outputPorts")):
        for port in rpg.get(key) or []:
            if isinstance(port, dict):
                contents.append(_remote_port_to_xml(port, tag, identifier))
    _set_text(node, "name", str(rpg.get("name") or ""))
    for key in ("localNetworkInterface", "proxyHost", "proxyPort", "proxyUser"):
        if rpg.get(key) not in (None, ""):
            _set_text(node, key, str(rpg.get(key)))
    target_uris = str(rpg.get("targetUris") or rpg.get("targetUri") or "")
    _set_text(node, "targetUri", str(rpg.get("targetUri") or target_uris.split(",")[0].strip()))
    _set_text(node, "targetUris", target_uris)
    _set_text(node, "transportProtocol", str(rpg.get("transportProtocol") or "RAW"))
    _set_text(node, "yieldDuration", str(rpg.get("yieldDuration") or "10 sec"))
    return node


def _remote_port_to_xml(port: dict[str, Any], tag: str, rpg_id: str) -> ET.Element:
    node = ET.Element(tag)
    identifier = str(port.get("identifier") or port.get("id") or _new_id(port))
    _set_text(node, "id", identifier)
    _set_text(node, "targetId", str(port.get("targetId") or identifier))
    _set_text(node, "groupId", rpg_id)
    _set_text(node, "name", str(port.get("name") or ""))
    comments = str(port.get("comments") or "")
    if comments:
        _set_text(node, "comments", comments)
    _set_text(
        node,
        "concurrentlySchedulableTaskCount",
        str(_int(str(port.get("concurrentlySchedulableTaskCount") or 1), 1)),
    )
    _set_text(node, "useCompression", "true" if port.get("useCompression") else "false")
    batch = port.get("batchSize")
    if isinstance(batch, dict) and batch:
        batch_el = ET.SubElement(node, "batchSettings")
        if batch.get("count"):
            _set_text(batch_el, "count", str(batch["count"]))
        if batch.get("size"):
            _set_text(batch_el, "size", str(batch["size"]))
        if batch.get("period"):
            _set_text(batch_el, "duration", str(batch["period"]))
    return node


def _bundle_xml(parent: ET.Element, bundle: Any, target_version: str) -> None:
    data = bundle if isinstance(bundle, dict) else {}
    node = ET.SubElement(parent, "bundle")
    _set_text(node, "group", str(data.get("group") or NIFI_BUNDLE_GROUP))
    _set_text(node, "artifact", str(data.get("artifact") or "nifi-standard-nar"))
    version = target_version
    if not follows_nifi_release(data) and data.get("version"):
        version = str(data["version"])
    _set_text(node, "version", version)


def _style_xml(parent: ET.Element, style: Any) -> None:
    if not isinstance(style, dict) or not style:
        return
    wrapper = ET.SubElement(parent, "style")
    for key, value in style.items():
        entry = ET.SubElement(wrapper, "entry")
        _set_text(entry, "key", str(key))
        _set_text(entry, "value", "" if value is None else str(value))


def _descriptors_xml(parent: ET.Element, descriptors: Any) -> None:
    if not isinstance(descriptors, dict) or not descriptors:
        return
    wrapper = ET.SubElement(parent, "descriptors")
    for key, data in descriptors.items():
        if not isinstance(data, dict):
            continue
        entry = ET.SubElement(wrapper, "entry")
        _set_text(entry, "key", str(key))
        value = ET.SubElement(entry, "value")
        name = str(data.get("name") or key)
        _set_text(value, "name", name)
        _set_text(value, "displayName", str(data.get("displayName") or name))
        _set_text(value, "dynamic", "true" if data.get("dynamic") else "false")
        _set_text(value, "sensitive", "true" if data.get("sensitive") else "false")


def _position_xml(parent: ET.Element, position: Any) -> None:
    data = position if isinstance(position, dict) else {}
    node = ET.SubElement(parent, "position")
    _set_text(node, "x", str(float(data.get("x") or 0)))
    _set_text(node, "y", str(float(data.get("y") or 0)))


def _properties_xml(parent: ET.Element, properties: Any) -> None:
    if not isinstance(properties, dict) or not properties:
        return
    wrapper = ET.SubElement(parent, "properties")
    for key, value in properties.items():
        entry = ET.SubElement(wrapper, "entry")
        _set_text(entry, "key", str(key))
        if value is not None:
            _set_text(entry, "value", str(value))


def _set_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child
