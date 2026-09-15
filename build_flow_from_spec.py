"""Build a NiFi process group from a JSON flow spec.

Supports nested sub-process groups and cross-group linking via ports, in
addition to flat processors/connections/controller services within one group.

Usage:
  python build_flow_from_spec.py path/to/spec.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from nifi_client import NiFiClient
from nifi_catalog import load_catalog

_REF = re.compile(r"^@(.+)$")
_PORT_KINDS = {"INPUT_PORT", "OUTPUT_PORT"}

# Properties NiFi renamed between releases, keyed by lowercased spec name. Lets one
# spec deploy to either version: whichever name the component declares is used.
_PROPERTY_ALIASES: dict[str, tuple[str, ...]] = {
    # 1.x called this "Jolt Transformation DSL"; 2.x calls it "Jolt Transform".
    # Getting it wrong leaves the default Chain transform, which rejects a shift spec.
    "jolt transformation dsl": ("Jolt Transform", "jolt-transform", "jolt-record-transform"),
    "jolt transform": ("Jolt Transformation DSL", "jolt-transform", "jolt-record-transform"),
    "jolt specification": ("jolt-record-spec", "jolt-spec"),
}

# Spec key -> NiFi processor `config` key. Both spellings are accepted, so a spec
# may use the friendly name or NiFi's own.
_PROCESSOR_CONFIG_KEYS = {
    "schedulingStrategy": "schedulingStrategy",
    "concurrentTasks": "concurrentlySchedulableTaskCount",
    "concurrentlySchedulableTaskCount": "concurrentlySchedulableTaskCount",
    "executionNode": "executionNode",
    "penaltyDuration": "penaltyDuration",
    "yieldDuration": "yieldDuration",
    "bulletinLevel": "bulletinLevel",
    "runDurationMillis": "runDurationMillis",
    "lossTolerant": "lossTolerant",
    "comments": "comments",
    "retryCount": "retryCount",
    "retriedRelationships": "retriedRelationships",
    "backoffMechanism": "backoffMechanism",
    "maxBackoffPeriod": "maxBackoffPeriod",
}

_CONNECTION_SETTING_KEYS = {
    "backPressureObjectThreshold": "backPressureObjectThreshold",
    "backPressureDataSizeThreshold": "backPressureDataSizeThreshold",
    "flowFileExpiration": "flowFileExpiration",
    "loadBalanceStrategy": "loadBalanceStrategy",
    "loadBalancePartitionAttribute": "loadBalancePartitionAttribute",
    "loadBalanceCompression": "loadBalanceCompression",
}

_PORT_EXTRA_KEYS = {
    "allowRemoteAccess": "allowRemoteAccess",
    "concurrentTasks": "concurrentlySchedulableTaskCount",
    "concurrentlySchedulableTaskCount": "concurrentlySchedulableTaskCount",
    "comments": "comments",
}

_GROUP_SETTING_KEYS = {
    "comments": "comments",
    "flowFileConcurrency": "flowfileConcurrency",
    "flowfileConcurrency": "flowfileConcurrency",
    "flowFileOutboundPolicy": "flowfileOutboundPolicy",
    "flowfileOutboundPolicy": "flowfileOutboundPolicy",
    "defaultFlowFileExpiration": "defaultFlowFileExpiration",
    "defaultBackPressureObjectThreshold": "defaultBackPressureObjectThreshold",
    "defaultBackPressureDataSizeThreshold": "defaultBackPressureDataSizeThreshold",
    # NiFi 2.x stateless execution engine. Absent on 1.x, where the gate drops them.
    "executionEngine": "executionEngine",
    "maxConcurrentTasks": "maxConcurrentTasks",
    "statelessFlowTimeout": "statelessFlowTimeout",
}

_PRIORITIZER_PREFIX = "org.apache.nifi.prioritizer."

# Which key in NiFi's own response proves a tuning field is supported. NiFi returns
# every field its version knows about (with defaults), so the live DTO — not a
# hardcoded version table — decides what this release accepts. `None` means the
# field cannot be probed (NiFi omits it when unset), so it is attempted and any
# rejection is handled as a dropped field.
_PROCESSOR_PROBES = {
    "retriedRelationships": "retryCount",
    "backoffMechanism": "retryCount",
    "maxBackoffPeriod": "retryCount",
}
_CONNECTION_PROBES = {
    "loadBalancePartitionAttribute": "loadBalanceStrategy",
    "loadBalanceCompression": "loadBalanceStrategy",
}
_PORT_PROBES = {
    "allowRemoteAccess": None,
    "comments": None,
}


def _split_supported(
    requested: dict[str, Any],
    supported_keys: set[str],
    probes: dict[str, str | None],
) -> tuple[dict[str, Any], list[str]]:
    """Split requested fields into those this NiFi version accepts and those it doesn't."""
    applied: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in requested.items():
        probe = probes.get(key, key)
        if probe is None or probe in supported_keys:
            applied[key] = value
        else:
            dropped.append(key)
    return applied, dropped


def _note_dropped(
    warnings: list[str], component: str, dropped: list[str], version: str
) -> None:
    if dropped:
        warnings.append(
            f"{component}: NiFi {version} does not support "
            f"{', '.join(sorted(dropped))} — field(s) skipped."
        )


def _subset(spec: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Translate the spec's keys into NiFi's, keeping only what was provided."""
    out: dict[str, Any] = {}
    for spec_key, nifi_key in mapping.items():
        if spec_key in spec and spec[spec_key] is not None:
            out[nifi_key] = spec[spec_key]
    return out


def _prioritizers(values: Any) -> list[str]:
    """Accept short prioritizer names as well as fully-qualified class names."""
    out = []
    for value in values or []:
        name = str(value)
        out.append(name if "." in name else _PRIORITIZER_PREFIX + name)
    return out


def _descriptor_index(
    descriptors: dict[str, Any] | None,
) -> dict[str, tuple[str, dict[str, Any]]] | None:
    """Index a component's descriptors by internal name AND by display name.

    The REST API only accepts a property's internal name (e.g. `jolt-record-spec`),
    but NiFi's UI and docs show display names (e.g. `Jolt Specification`), which is
    what flow authors naturally write. Indexing both lets either form work.
    """
    if not descriptors:
        return None
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for name, desc in descriptors.items():
        index[name] = (name, desc)
        display = desc.get("displayName")
        if display:
            index.setdefault(display, (name, desc))
    return index


def _coerce_allowable(desc: dict[str, Any], value: str) -> str:
    """Map an allowable value's display label to the value the API expects.

    e.g. the Jolt DSL shows `Shift` but stores `jolt-transform-shift`.
    """
    entries = desc.get("allowableValues") or []
    if not entries:
        return value
    values: set[str] = set()
    by_display: dict[str, str] = {}
    for entry in entries:
        allowable = entry.get("allowableValue") or {}
        raw = allowable.get("value")
        if raw is not None:
            values.add(str(raw))
            display = allowable.get("displayName")
            if display:
                by_display.setdefault(str(display), str(raw))
    text = str(value)
    if text in values:
        return text
    if text in by_display:
        return by_display[text]
    lowered = {key.lower(): val for key, val in by_display.items()}
    return lowered.get(text.lower(), text)


def _normalize_props(
    props: dict[str, str],
    descriptors: dict[str, Any] | None,
    dropped: list[str] | None = None,
) -> dict[str, str]:
    """Translate property keys/values to what NiFi expects, dropping unknown keys.

    Unknown keys are collected in `dropped` rather than vanishing: a property the
    component does not declare is usually a cross-version rename or a typo, and
    silently discarding a *required* one leaves the processor invalid for reasons
    that are very hard to trace back.
    """
    index = _descriptor_index(descriptors)
    if index is None:
        return dict(props)
    lowered = {key.lower(): value for key, value in index.items()}
    out: dict[str, str] = {}
    for key, value in props.items():
        hit = index.get(key) or lowered.get(str(key).lower())
        if hit is None:
            for alias in _PROPERTY_ALIASES.get(str(key).lower(), ()):
                hit = index.get(alias) or lowered.get(alias.lower())
                if hit:
                    break
        if hit is None:
            if dropped is not None:
                dropped.append(str(key))
            continue
        name, desc = hit
        out[name] = _coerce_allowable(desc, value)
    return out


def _resolve_service_props(props: dict[str, str], services: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in props.items():
        match = _REF.match(str(value).strip())
        if match:
            ref = match.group(1)
            if ref not in services:
                raise KeyError(f"Unknown controller service reference @{ref}")
            out[key] = services[ref]
        else:
            out[key] = value
    return out


def _resolve_connectable(
    ref: str,
    local: dict[str, dict[str, Any]],
    children: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Resolve a connection endpoint name to {id, type, groupId}.

    A bare name (e.g. "LogAttribute") refers to a processor/port/funnel created
    directly in the current process group. A dotted name (e.g. "SubFlow.Input")
    refers to a port exposed by a nested child process group named "SubFlow" —
    this is how NiFi links flow across process-group boundaries.
    """
    if "." in ref:
        group_name, port_name = ref.split(".", 1)
        ports = children.get(group_name)
        if ports is None:
            raise KeyError(f"Unknown child process group '{group_name}' referenced as '{ref}'")
        entry = ports.get(port_name)
        if entry is None:
            raise KeyError(
                f"Unknown port '{port_name}' on child process group '{group_name}' (available: {list(ports)})"
            )
        return entry
    entry = local.get(ref)
    if entry is None:
        raise KeyError(f"Unknown connectable '{ref}' (available: {list(local)})")
    return entry


def _create_controller_services(
    client: NiFiClient,
    catalog: Any,
    pg_id: str,
    specs: list[dict[str, Any]],
    inherited_services: dict[str, str],
    warnings: list[str] | None = None,
) -> dict[str, str]:
    services: dict[str, str] = dict(inherited_services)
    for svc in specs or []:
        svc_info = catalog.resolve_service_info(svc["type"])
        # Create bare, then configure: properties set at creation time bypass descriptor
        # normalization, and an unknown key there leaves the service permanently invalid.
        created = client.create_controller_service(
            pg_id,
            name=svc["name"],
            type_name=svc_info.type_name,
            properties={},
            bundle=svc.get("bundle") or svc_info.bundle,
        )
        svc_id = created["id"]
        fresh = client.get(f"/nifi-api/controller-services/{svc_id}")
        descriptors = fresh.get("component", {}).get("descriptors")
        unknown: list[str] = []
        props = _normalize_props(svc.get("properties") or {}, descriptors, unknown)
        props.update(svc.get("dynamicProperties") or {})
        # Resolve @ServiceName the same way processors do, so a lookup/DB
        # service can reference a pool created earlier in this (or a parent) group.
        props = _resolve_service_props(props, services)
        if unknown and warnings is not None:
            warnings.append(
                f"Controller service '{svc['name']}' ({svc['type']}): "
                f"{', '.join(sorted(unknown))} is not a property of this component "
                f"on NiFi {catalog.version} — ignored."
            )
        if props:
            client.put(
                f"/nifi-api/controller-services/{svc_id}",
                {
                    "revision": {
                        "version": fresh["revision"]["version"],
                        "clientId": fresh["revision"].get("clientId"),
                    },
                    "component": {
                        "id": svc_id,
                        "name": svc["name"],
                        "properties": props,
                    },
                },
            )
        client.enable_controller_service(svc_id)
        services[svc["name"]] = svc_id
        # Enabling is asynchronous: a processor wired to a service that is still
        # ENABLING is created invalid and will not start.
        state = client.wait_for_controller_service(svc_id)
        if state != "ENABLED":
            raise RuntimeError(
                f"Controller service '{svc['name']}' did not enable (state={state or 'unknown'})."
            )
    return services


def _build_group(
    client: NiFiClient,
    catalog: Any,
    parent_id: str,
    group: dict[str, Any],
    inherited_services: dict[str, str],
    index: int = 0,
    parameter_contexts: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Recursively create a process group (and any nested sub-groups) from spec.

    Returns a summary dict including this group's id, its processors/ports/
    funnels/connections, and a `ports` map ({name: {id, type, groupId}}) used
    by the parent to resolve cross-group connection references.
    """
    parameter_contexts = parameter_contexts or {}
    warnings = warnings if warnings is not None else []
    name = group.get("processGroupName") or group.get("name") or f"group-{index}"
    position = group.get("position") or {"x": group.get("x", 200), "y": group.get("y", 200)}

    if group.get("replaceExisting"):
        existing = client.find_child_group_id(parent_id, name)
        if existing:
            client.delete_process_group(existing)

    pg = client.create_process_group(
        parent_id,
        name,
        x=float(position.get("x", 200)),
        y=float(position.get("y", 200)),
    )
    pg_id = pg["id"]

    group_settings, dropped = _split_supported(
        _subset(group, _GROUP_SETTING_KEYS), set((pg.get("component") or {}).keys()), {}
    )
    _note_dropped(warnings, f"Process group '{name}'", dropped, catalog.version)
    context_name = group.get("parameterContext")
    if context_name:
        context_id = parameter_contexts.get(str(context_name))
        if not context_id:
            raise ValueError(
                f"Group '{name}' references parameter context '{context_name}', "
                "which is not declared in the spec's top-level parameterContexts."
            )
        group_settings["parameterContext"] = {"id": context_id}
    if group_settings:
        client.update_process_group(pg_id, **group_settings)

    services = _create_controller_services(
        client,
        catalog,
        pg_id,
        group.get("controllerServices") or [],
        inherited_services,
        warnings=warnings,
    )

    local: dict[str, dict[str, Any]] = {}

    for i, proc in enumerate(group.get("processors") or []):
        pname = proc["name"]
        props = _resolve_service_props(proc.get("properties") or {}, services)
        # User-defined properties (e.g. the attributes UpdateAttribute sets) are absent
        # from a processor's descriptors, so they must bypass the descriptor filter.
        dynamic = _resolve_service_props(proc.get("dynamicProperties") or {}, services)
        proc_info = catalog.resolve_processor_info(proc["type"])
        config_extra = _subset(proc, _PROCESSOR_CONFIG_KEYS)
        # Create bare, then configure — see the note in _create_controller_services.
        # Scheduling is deliberately left out of the create call: a CRON period is
        # rejected while the strategy is still the default TIMER_DRIVEN, so the two
        # have to be applied together in the update below.
        created = client.create_processor(
            pg_id,
            name=pname,
            type_name=proc_info.type_name,
            x=float(proc.get("x", 100 + i * 300)),
            y=float(proc.get("y", 100)),
            properties={},
            bundle=proc.get("bundle") or proc_info.bundle,
        )
        proc_id = created["id"]
        fresh = client.get(f"/nifi-api/processors/{proc_id}")
        component = fresh.get("component") or {}
        live_config = component.get("config") or {}
        descriptors = live_config.get("descriptors") or component.get("descriptors")
        unknown: list[str] = []
        props = {**_normalize_props(props, descriptors, unknown), **dynamic}
        if unknown:
            warnings.append(
                f"Processor '{pname}' ({proc['type']}): "
                f"{', '.join(sorted(unknown))} is not a property of this component on "
                f"NiFi {catalog.version} — ignored. Put user-defined properties under "
                "'dynamicProperties'."
            )

        config_extra, dropped = _split_supported(
            config_extra, set(live_config.keys()), _PROCESSOR_PROBES
        )
        if (
            config_extra.get("schedulingStrategy") == "EVENT_DRIVEN"
            and not component.get("supportsEventDriven")
        ):
            # Removed in NiFi 2.x, and unsupported by most processors before that.
            config_extra.pop("schedulingStrategy")
            dropped.append("schedulingStrategy=EVENT_DRIVEN")
        _note_dropped(warnings, f"Processor '{pname}'", dropped, catalog.version)

        client.update_processor(
            proc_id,
            revision_version=fresh["revision"]["version"],
            properties=props,
            auto_terminated=proc.get("autoTerminated") or [],
            scheduling_period=proc.get("schedulingPeriod"),
            config_extra=config_extra or None,
        )
        local[pname] = {"id": proc_id, "type": "PROCESSOR", "groupId": pg_id}

    for i, port in enumerate(group.get("ports") or []):
        pname = port["name"]
        kind = str(port.get("kind") or port.get("type") or "input").strip().lower()
        x = float(port.get("x", 100 + i * 240))
        y = float(port.get("y", 320))
        extra = _subset(port, _PORT_EXTRA_KEYS) or None
        if kind in ("input", "input_port"):
            created = client.create_input_port(pg_id, pname, x=x, y=y, extra=extra)
            port_type = "INPUT_PORT"
        elif kind in ("output", "output_port"):
            created = client.create_output_port(pg_id, pname, x=x, y=y, extra=extra)
            port_type = "OUTPUT_PORT"
        else:
            raise ValueError(f"Port '{pname}' has unknown kind '{kind}' (use 'input' or 'output')")
        if extra:
            # Concurrency and remote access are dropped by the create call, so
            # re-apply them now, while the port is still stopped.
            extra, dropped = _split_supported(
                extra, set((created.get("component") or {}).keys()), _PORT_PROBES
            )
            _note_dropped(warnings, f"Port '{pname}'", dropped, catalog.version)
            if extra:
                client.update_port(created["id"], port_type, **extra)
        local[pname] = {"id": created["id"], "type": port_type, "groupId": pg_id}

    for i, funnel in enumerate(group.get("funnels") or []):
        fname = funnel.get("name") or f"funnel-{i}"
        created = client.create_funnel(
            pg_id, x=float(funnel.get("x", 500)), y=float(funnel.get("y", 400 + i * 150))
        )
        local[fname] = {"id": created["id"], "type": "FUNNEL", "groupId": pg_id}

    for i, label in enumerate(group.get("labels") or []):
        client.create_label(
            pg_id,
            text=label.get("text") or "",
            x=float(label.get("x", 100)),
            y=float(label.get("y", 500 + i * 140)),
            width=float(label.get("width", 260)),
            height=float(label.get("height", 120)),
        )

    children: dict[str, dict[str, dict[str, Any]]] = {}
    child_summaries: dict[str, dict[str, Any]] = {}
    for i, remote in enumerate(group.get("remoteProcessGroups") or []):
        created = client.create_remote_process_group(
            pg_id,
            target_uris=remote["targetUris"],
            x=float(remote.get("x", 100 + i * 320)),
            y=float(remote.get("y", 640)),
            transport_protocol=remote.get("transportProtocol", "HTTP"),
            extra=remote.get("extra"),
        )
        rname = remote.get("name") or f"remote-{i}"
        local[rname] = {
            "id": created["id"],
            "type": "REMOTE_PROCESS_GROUP",
            "groupId": pg_id,
        }

    for i, child_group in enumerate(group.get("processGroups") or []):
        child_result = _build_group(
            client,
            catalog,
            pg_id,
            child_group,
            services,
            index=i,
            parameter_contexts=parameter_contexts,
            warnings=warnings,
        )
        child_name = child_result["name"]
        children[child_name] = child_result["ports"]
        child_summaries[child_name] = child_result

    connections_out = []
    for conn in group.get("connections") or []:
        src = _resolve_connectable(conn["from"], local, children)
        dst = _resolve_connectable(conn["to"], local, children)
        # Only processors have named relationships. Ports and funnels reject them
        # ("No relationship with name success exists for Local Ports"), so drop
        # whatever the spec wrote for those sources.
        relationships = (
            list(conn.get("relationships") or ["success"])
            if src["type"] == "PROCESSOR"
            else []
        )
        settings = _subset(conn, _CONNECTION_SETTING_KEYS)
        if conn.get("prioritizers"):
            settings["prioritizers"] = _prioritizers(conn["prioritizers"])
        created_conn = client.create_connection(
            pg_id,
            source_id=src["id"],
            destination_id=dst["id"],
            relationships=relationships,
            name=conn.get("name") or "",
            source_type=src["type"],
            destination_type=dst["type"],
            source_group_id=src["groupId"],
            destination_group_id=dst["groupId"],
        )
        if settings:
            # Applied after create so the live DTO can veto fields this version lacks.
            settings, dropped = _split_supported(
                settings,
                set((created_conn.get("component") or {}).keys()),
                _CONNECTION_PROBES,
            )
            label = conn.get("name") or f"{conn.get('from')} -> {conn.get('to')}"
            _note_dropped(warnings, f"Connection '{label}'", dropped, catalog.version)
            if settings:
                client.update_connection(created_conn["id"], settings)
        connections_out.append(created_conn["id"])

    started = bool(group.get("start", True))
    if started:
        client.start_process_group(pg_id)

    ports = {pname: entry for pname, entry in local.items() if entry["type"] in _PORT_KINDS}

    return {
        "id": pg_id,
        "name": name,
        "processors": {n: e["id"] for n, e in local.items() if e["type"] == "PROCESSOR"},
        "ports": ports,
        "funnels": {n: e["id"] for n, e in local.items() if e["type"] == "FUNNEL"},
        "remoteProcessGroups": {
            n: e["id"] for n, e in local.items() if e["type"] == "REMOTE_PROCESS_GROUP"
        },
        "controllerServices": services,
        "connections": connections_out,
        "processGroups": child_summaries,
        "started": started,
    }


def _create_parameter_contexts(
    client: NiFiClient, contexts: list[dict[str, Any]]
) -> dict[str, str]:
    """Create (or reuse) parameter contexts and return {name: id}."""
    if not contexts:
        return {}
    existing = {
        (c.get("component") or {}).get("name"): c.get("id")
        for c in client.list_parameter_contexts()
    }
    out: dict[str, str] = {}
    for context in contexts:
        name = context["name"]
        if name in existing and existing[name]:
            out[name] = str(existing[name])
            continue
        created = client.create_parameter_context(
            name,
            parameters=context.get("parameters") or [],
            description=context.get("description", ""),
        )
        out[name] = created["id"]
    return out


def _create_controller_level(
    client: NiFiClient, catalog: Any, services: list[dict[str, Any]]
) -> dict[str, str]:
    existing = {
        (c.get("component") or {}).get("name"): c.get("id")
        for c in (client.get("/nifi-api/flow/controller/controller-services") or {}).get(
            "controllerServices"
        )
        or []
    }
    out: dict[str, str] = {}
    for svc in services or []:
        if svc["name"] in existing and existing[svc["name"]]:
            out[svc["name"]] = str(existing[svc["name"]])
            continue
        info = catalog.resolve_service_info(svc["type"])
        created = client.create_controller_level_service(
            svc["name"],
            type_name=info.type_name,
            bundle=svc.get("bundle") or info.bundle,
        )
        svc_id = created["id"]
        fresh = client.get(f"/nifi-api/controller-services/{svc_id}")
        descriptors = fresh.get("component", {}).get("descriptors")
        props = _normalize_props(svc.get("properties") or {}, descriptors)
        props.update(svc.get("dynamicProperties") or {})
        if props:
            client.put(
                f"/nifi-api/controller-services/{svc_id}",
                {
                    "revision": {
                        "version": fresh["revision"]["version"],
                        "clientId": fresh["revision"].get("clientId"),
                    },
                    "component": {"id": svc_id, "name": svc["name"], "properties": props},
                },
            )
        out[svc["name"]] = svc_id
        client.enable_controller_service(svc_id)
    return out


def _create_reporting_tasks(
    client: NiFiClient, tasks: list[dict[str, Any]]
) -> dict[str, str]:
    if not tasks:
        return {}
    available = {}
    for item in client.list_reporting_task_types():
        type_name = item.get("type") or ""
        available[type_name] = item.get("bundle")
        available[type_name.rsplit(".", 1)[-1]] = item.get("bundle")

    existing = {
        (t.get("component") or {}).get("name"): t.get("id")
        for t in (client.get("/nifi-api/flow/reporting-tasks") or {}).get("reportingTasks") or []
    }
    out: dict[str, str] = {}
    for task in tasks:
        # Reporting tasks are controller-scoped, so redeploying a flow must not
        # stack up another copy of the same task.
        if task["name"] in existing and existing[task["name"]]:
            out[task["name"]] = str(existing[task["name"]])
            continue
        requested = task["type"]
        if requested not in available:
            raise ValueError(
                f"Reporting task type '{requested}' is not installed on this NiFi. "
                f"Available example: {sorted(k for k in available if '.' in k)[:3]}"
            )
        full_type = requested if "." in requested else next(
            k for k in available if k.endswith("." + requested)
        )
        created = client.create_reporting_task(
            task["name"],
            type_name=full_type,
            scheduling_period=task.get("schedulingPeriod"),
            bundle=task.get("bundle") or available.get(requested),
        )
        task_id = created["id"]
        fresh = client.get(f"/nifi-api/reporting-tasks/{task_id}")
        descriptors = fresh.get("component", {}).get("descriptors")
        props = _normalize_props(task.get("properties") or {}, descriptors)
        props.update(task.get("dynamicProperties") or {})
        if props:
            client.put(
                f"/nifi-api/reporting-tasks/{task_id}",
                {
                    "revision": {
                        "version": fresh["revision"]["version"],
                        "clientId": fresh["revision"].get("clientId"),
                    },
                    "component": {"id": task_id, "name": task["name"], "properties": props},
                },
            )
        out[task["name"]] = task_id
        if task.get("start", True):
            client.set_reporting_task_state(task_id, "RUNNING")
    return out


def build_from_spec(spec: dict[str, Any], client: NiFiClient | None = None) -> dict[str, Any]:
    client = client or NiFiClient()
    client.login()
    catalog = load_catalog(client)

    # Assign x/y for components the spec didn't hand-place, so agent-generated
    # flows read as clean left-to-right diagrams. Opt out per group with
    # `"layout": "manual"`.
    from flow_layout import apply_layout

    apply_layout(spec)

    # Controller-scoped objects must exist before groups reference them.
    parameter_contexts = _create_parameter_contexts(client, spec.get("parameterContexts") or [])
    controller_services = _create_controller_level(
        client, catalog, spec.get("controllerLevelServices") or []
    )
    reporting_tasks = _create_reporting_tasks(client, spec.get("reportingTasks") or [])

    warnings: list[str] = []
    root_id = client.root_process_group_id()
    result = _build_group(
        client,
        catalog,
        root_id,
        spec,
        inherited_services={},
        parameter_contexts=parameter_contexts,
        warnings=warnings,
    )

    return {
        "processGroupId": result["id"],
        "processGroupName": result["name"],
        "processors": result["processors"],
        "ports": {n: e["id"] for n, e in result["ports"].items()},
        "funnels": result["funnels"],
        "remoteProcessGroups": result["remoteProcessGroups"],
        "controllerServices": result["controllerServices"],
        "connections": result["connections"],
        "processGroups": result["processGroups"],
        "parameterContexts": parameter_contexts,
        "controllerLevelServices": controller_services,
        "reportingTasks": reporting_tasks,
        "started": result["started"],
        "warnings": warnings,
        "nifiVersion": spec.get("nifiVersion") or catalog.version,
        "processorBundleVersion": catalog.version,
        "jsonToCsvStrategy": spec.get("jsonToCsvStrategy"),
        "ui": f"{client.base_url}/nifi/",
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python build_flow_from_spec.py <spec.json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    spec = json.loads(path.read_text(encoding="utf-8"))
    result = build_from_spec(spec)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
