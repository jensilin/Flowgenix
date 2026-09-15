"""Read-only deploy plan for a flow spec.

Produces a summary of what `build_flow_from_spec.build_from_spec` *would* do,
without touching NiFi state. Used by the review-before-deploy UI so users see:

- what components a spec creates (counts, tree, referenced processor/service types)
- what would collide with the live canvas (same-named groups, and whether
  `replaceExisting` will overwrite them and how many FlowFiles that will lose)
- what the plan can prove is wrong up front (processor type not installed on
  this NiFi release, port kind missing, `EVENT_DRIVEN` requested on 2.x, ...)

Descriptor-level checks (property renames, allowable-value coercion) still
happen at real deploy time — creating a processor is the only way to read its
descriptors, and the plan must not mutate state. Those show up in the deploy
result's `warnings`; the review step doesn't skip them, only gates on user
approval before applying.
"""

from __future__ import annotations

from typing import Any

from build_flow_from_spec import _PORT_KINDS
from nifi_catalog import NiFiCatalog
from nifi_client import NiFiClient


# Processor scheduling / retry / execution knobs we can meaningfully validate
# statically. Everything else in the spec goes through the builder's own
# adaptive gate at deploy time.
_PROCESSOR_KNOBS = (
    "schedulingStrategy",
    "schedulingPeriod",
    "concurrentTasks",
    "executionNode",
    "runDurationMillis",
    "retryCount",
    "retriedRelationships",
    "backoffMechanism",
    "maxBackoffPeriod",
)


def build_plan(
    spec: dict[str, Any],
    client: NiFiClient,
    catalog: NiFiCatalog,
) -> dict[str, Any]:
    """Return a deploy plan for `spec`.

    Never modifies NiFi state; only reads (`/flow/process-groups/...`).
    """
    warnings: list[str] = []
    root_id = client.root_process_group_id()
    tree = _plan_group(client, catalog, root_id, spec, path="/", warnings=warnings)
    counts = _totals(tree)
    return {
        "nifiVersion": catalog.version,
        "target": {
            "uiUrl": f"{client.base_url}/nifi/",
            "rootId": root_id,
        },
        "summary": {
            "processGroups": counts["processGroups"],
            "processors": counts["processors"],
            "controllerServices": counts["controllerServices"],
            "connections": counts["connections"],
            "inputPorts": counts["inputPorts"],
            "outputPorts": counts["outputPorts"],
            "funnels": counts["funnels"],
            "labels": counts["labels"],
            "remoteProcessGroups": counts["remoteProcessGroups"],
            "willReplace": counts["willReplace"],
            "conflicts": counts["conflicts"],
            "missingTypes": counts["missingTypes"],
            "warningCount": len(warnings),
        },
        "parameterContexts": [
            {
                "name": ctx.get("name"),
                "parameters": [p.get("name") for p in ctx.get("parameters") or []],
            }
            for ctx in (spec.get("parameterContexts") or [])
        ],
        "reportingTasks": [
            {"name": rt.get("name"), "type": rt.get("type")}
            for rt in (spec.get("reportingTasks") or [])
        ],
        "controllerLevelServices": [
            {"name": svc.get("name"), "type": svc.get("type")}
            for svc in (spec.get("controllerLevelServices") or [])
        ],
        "tree": tree,
        "warnings": warnings,
    }


def _plan_group(
    client: NiFiClient,
    catalog: NiFiCatalog,
    parent_id: str,
    group: dict[str, Any],
    path: str,
    warnings: list[str],
) -> dict[str, Any]:
    name = group.get("processGroupName") or group.get("name") or "(unnamed)"
    existing_id = client.find_child_group_id(parent_id, name) if parent_id else None
    replace = bool(group.get("replaceExisting"))

    conflict: dict[str, Any] | None = None
    if existing_id:
        existing = _summarize_live_group(client, existing_id)
        action = "replace" if replace else "collision"
        conflict = {
            "existingId": existing_id,
            "action": action,
            "existing": existing,
        }
        if action == "collision":
            warnings.append(
                f"Group '{name}' at {path} already exists on the canvas and "
                "`replaceExisting` is not set — deploying will create a second copy "
                "with the same name."
            )
        elif existing.get("flowFilesQueued"):
            warnings.append(
                f"Group '{name}' at {path} will be replaced, dropping "
                f"{existing['flowFilesQueued']} queued FlowFile(s)."
            )

    proc_planned: list[dict[str, Any]] = []
    for proc in group.get("processors") or []:
        proc_planned.append(_plan_processor(catalog, proc, warnings, path, name))

    services_planned: list[dict[str, Any]] = []
    for svc in group.get("controllerServices") or []:
        services_planned.append(_plan_service(catalog, svc, warnings, path, name))

    connections_planned: list[dict[str, Any]] = []
    for conn in group.get("connections") or []:
        connections_planned.append(_plan_connection(conn))

    ports_planned: list[dict[str, Any]] = []
    for port in group.get("ports") or []:
        ports_planned.append(_plan_port(port, warnings, path, name))

    child_path = f"{path}{name}/"
    child_groups: list[dict[str, Any]] = []
    for child in group.get("processGroups") or []:
        # If the parent will be replaced, children start fresh: don't check for
        # collisions against the doomed group's contents.
        child_parent = existing_id if (existing_id and not replace) else None
        child_groups.append(
            _plan_group(client, catalog, child_parent or "", child, child_path, warnings)
        )

    return {
        "name": name,
        "path": path,
        "comments": group.get("comments"),
        "start": bool(group.get("start")),
        "parameterContext": group.get("parameterContext"),
        "replaceExisting": replace,
        "conflict": conflict,
        "processors": proc_planned,
        "controllerServices": services_planned,
        "connections": connections_planned,
        "ports": ports_planned,
        "funnels": [{"name": f.get("name")} for f in (group.get("funnels") or [])],
        "labels": [
            {"text": (l.get("text") or "").splitlines()[0] if l.get("text") else ""}
            for l in (group.get("labels") or [])
        ],
        "remoteProcessGroups": [
            {"targetUris": r.get("targetUris"), "transportProtocol": r.get("transportProtocol")}
            for r in (group.get("remoteProcessGroups") or [])
        ],
        "processGroups": child_groups,
    }


def _plan_processor(
    catalog: NiFiCatalog,
    proc: dict[str, Any],
    warnings: list[str],
    path: str,
    group_name: str,
) -> dict[str, Any]:
    name = proc.get("name") or "(unnamed)"
    type_hint = str(proc.get("type") or "")
    info = catalog.processor_info(type_hint)
    installed = info is not None
    if not installed:
        warnings.append(
            f"Processor '{name}' in group '{group_name}' at {path}: "
            f"type '{type_hint}' is not installed on NiFi {catalog.version} — "
            "deploy will fail unless the spec uses a type this instance has."
        )

    knobs: dict[str, Any] = {}
    for key in _PROCESSOR_KNOBS:
        if key in proc:
            knobs[key] = proc[key]

    return {
        "name": name,
        "type": type_hint,
        "resolvedType": info.type_name if info else None,
        "bundle": info.bundle if info else None,
        "installed": installed,
        "autoTerminated": list(proc.get("autoTerminated") or []),
        "propertyCount": len(proc.get("properties") or {}),
        "dynamicPropertyCount": len(proc.get("dynamicProperties") or {}),
        "scheduling": knobs,
    }


def _plan_service(
    catalog: NiFiCatalog,
    svc: dict[str, Any],
    warnings: list[str],
    path: str,
    group_name: str,
) -> dict[str, Any]:
    name = svc.get("name") or "(unnamed)"
    type_hint = str(svc.get("type") or "")
    info = catalog.service_info(type_hint)
    installed = info is not None
    if not installed:
        warnings.append(
            f"Controller service '{name}' in group '{group_name}' at {path}: "
            f"type '{type_hint}' is not installed on NiFi {catalog.version} — "
            "deploy will fail unless the spec uses a type this instance has."
        )
    return {
        "name": name,
        "type": type_hint,
        "resolvedType": info.type_name if info else None,
        "installed": installed,
        "propertyCount": len(svc.get("properties") or {}),
    }


def _plan_connection(conn: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": conn.get("from"),
        "to": conn.get("to"),
        "relationships": list(conn.get("relationships") or []),
        "name": conn.get("name"),
        "backPressureObjectThreshold": conn.get("backPressureObjectThreshold"),
        "flowFileExpiration": conn.get("flowFileExpiration"),
        "prioritizers": conn.get("prioritizers"),
        "loadBalanceStrategy": conn.get("loadBalanceStrategy"),
    }


def _plan_port(
    port: dict[str, Any],
    warnings: list[str],
    path: str,
    group_name: str,
) -> dict[str, Any]:
    kind_raw = str(port.get("kind") or "").upper()
    kind = (
        "INPUT_PORT" if kind_raw in {"INPUT_PORT", "INPUT", "IN"}
        else "OUTPUT_PORT" if kind_raw in {"OUTPUT_PORT", "OUTPUT", "OUT"}
        else "UNKNOWN"
    )
    if kind not in _PORT_KINDS:
        warnings.append(
            f"Port '{port.get('name')}' in group '{group_name}' at {path}: "
            f"'kind' must be input/output, got {port.get('kind')!r}."
        )
    return {
        "name": port.get("name"),
        "kind": kind,
        "concurrentTasks": port.get("concurrentTasks"),
        "allowRemoteAccess": port.get("allowRemoteAccess"),
    }


def _summarize_live_group(client: NiFiClient, group_id: str) -> dict[str, Any]:
    """Read the live group so the UI can show what `replaceExisting` will destroy."""
    data = client.get(f"/nifi-api/flow/process-groups/{group_id}") or {}
    flow = ((data.get("processGroupFlow") or {}).get("flow")) or {}
    queued = 0
    for conn in flow.get("connections") or []:
        status = (conn.get("status") or {}).get("aggregateSnapshot") or {}
        queued += int(status.get("flowFilesQueued") or 0)
    return {
        "processors": len(flow.get("processors") or []),
        "controllerServices": len(flow.get("controllerServices") or []),
        "connections": len(flow.get("connections") or []),
        "inputPorts": len(flow.get("inputPorts") or []),
        "outputPorts": len(flow.get("outputPorts") or []),
        "processGroups": len(flow.get("processGroups") or []),
        "flowFilesQueued": queued,
    }


def _totals(tree: dict[str, Any]) -> dict[str, int]:
    counts = {
        "processGroups": 0,
        "processors": 0,
        "controllerServices": 0,
        "connections": 0,
        "inputPorts": 0,
        "outputPorts": 0,
        "funnels": 0,
        "labels": 0,
        "remoteProcessGroups": 0,
        "willReplace": 0,
        "conflicts": 0,
        "missingTypes": 0,
    }

    def visit(node: dict[str, Any]) -> None:
        counts["processGroups"] += 1
        counts["processors"] += len(node.get("processors") or [])
        counts["controllerServices"] += len(node.get("controllerServices") or [])
        counts["connections"] += len(node.get("connections") or [])
        counts["inputPorts"] += sum(
            1 for p in node.get("ports") or [] if p.get("kind") == "INPUT_PORT"
        )
        counts["outputPorts"] += sum(
            1 for p in node.get("ports") or [] if p.get("kind") == "OUTPUT_PORT"
        )
        counts["funnels"] += len(node.get("funnels") or [])
        counts["labels"] += len(node.get("labels") or [])
        counts["remoteProcessGroups"] += len(node.get("remoteProcessGroups") or [])
        counts["missingTypes"] += sum(
            1 for p in node.get("processors") or [] if not p.get("installed")
        )
        counts["missingTypes"] += sum(
            1 for s in node.get("controllerServices") or [] if not s.get("installed")
        )
        conflict = node.get("conflict")
        if conflict:
            if conflict.get("action") == "replace":
                counts["willReplace"] += 1
            elif conflict.get("action") == "collision":
                counts["conflicts"] += 1
        for child in node.get("processGroups") or []:
            visit(child)

    visit(tree)
    return counts
