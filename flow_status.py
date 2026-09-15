"""Point-in-time status snapshot for a deployed process group.

Called by the observability panel every ~2s. Returns everything the UI needs
to render in a single request — status counts, per-processor throughput,
per-connection queue depth, and recent bulletins — so the frontend can just
poll one URL without stitching multiple NiFi endpoints together itself.
"""

from __future__ import annotations

import time
from typing import Any

from nifi_client import NiFiClient


def snapshot(client: NiFiClient, group_id: str) -> dict[str, Any]:
    """Return a bulk status snapshot for `group_id`.

    Reads three NiFi endpoints:
    - /flow/process-groups/{id}/status?recursive=true - aggregate + per-component counts
    - /flow/process-groups/{id}/bulletins            - recent errors/warnings
    - /flow/process-groups/{id}                      - to get the human-readable names
       (the status endpoint returns names too, so this is only used when the status
       payload is missing them, which the older NiFi versions occasionally do).
    """
    as_of = int(time.time() * 1000)
    status_env = client.get(f"/nifi-api/flow/process-groups/{group_id}/status?recursive=true") or {}
    status = ((status_env.get("processGroupStatus") or {})
              .get("aggregateSnapshot")) or {}
    # /flow/bulletin-board is a single global endpoint; filter it to this group.
    # NiFi 1.x accepts `groupId`; the response wraps bulletins under
    # `bulletinBoard.bulletins`. Either the empty-body path or an unsupported
    # server returns something without `bulletins`, which we tolerate.
    bulletins_env = (
        client.get(
            f"/nifi-api/flow/bulletin-board?groupId={group_id}&limit=50"
        )
        or {}
    )

    processors = [_processor_row(p) for p in status.get("processorStatusSnapshots") or []]
    connections = [_connection_row(c) for c in status.get("connectionStatusSnapshots") or []]
    input_ports = [_port_row(p) for p in status.get("inputPortStatusSnapshots") or []]
    output_ports = [_port_row(p) for p in status.get("outputPortStatusSnapshots") or []]

    aggregate = _aggregate(status, processors)

    bulletins = []
    for entry in ((bulletins_env.get("bulletinBoard") or {}).get("bulletins")) or []:
        b = entry.get("bulletin") or entry
        bulletins.append({
            "id": entry.get("id") or b.get("id"),
            "timestamp": b.get("timestamp"),
            "level": b.get("level"),
            "sourceName": b.get("sourceName"),
            "category": b.get("category"),
            "message": b.get("message"),
            "sourceType": b.get("sourceType"),
        })
    bulletins.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {
        "processGroupId": group_id,
        "asOf": as_of,
        "name": status.get("name"),
        "aggregate": aggregate,
        "processors": processors,
        "connections": connections,
        "inputPorts": input_ports,
        "outputPorts": output_ports,
        "bulletins": bulletins,
    }


def _aggregate(status: dict[str, Any], processors: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"running": 0, "stopped": 0, "invalid": 0, "disabled": 0}
    for p in processors:
        run = (p.get("runStatus") or "").lower()
        if run == "running":
            counts["running"] += 1
        elif run == "invalid":
            counts["invalid"] += 1
        elif run == "disabled":
            counts["disabled"] += 1
        else:
            counts["stopped"] += 1
    return {
        "runStatus": status.get("runStatus"),
        "processorCount": len(processors),
        "running": counts["running"],
        "stopped": counts["stopped"],
        "invalid": counts["invalid"],
        "disabled": counts["disabled"],
        "flowFilesIn": status.get("flowFilesIn") or 0,
        "flowFilesOut": status.get("flowFilesOut") or 0,
        "bytesIn": status.get("bytesIn") or 0,
        "bytesOut": status.get("bytesOut") or 0,
        "flowFilesQueued": status.get("flowFilesQueued") or 0,
        "bytesQueued": status.get("bytesQueued") or 0,
        "activeThreadCount": status.get("activeThreadCount") or 0,
        "flowFilesReceived": status.get("flowFilesReceived") or 0,
        "flowFilesSent": status.get("flowFilesSent") or 0,
        # Pretty-printed counters, as NiFi does in the UI. Keeps the client simple.
        "input": status.get("input"),
        "output": status.get("output"),
        "read": status.get("read"),
        "written": status.get("written"),
        "queued": status.get("queued"),
    }


def _processor_row(entry: dict[str, Any]) -> dict[str, Any]:
    snap = entry.get("processorStatusSnapshot") or entry
    return {
        "id": snap.get("id"),
        "name": snap.get("name"),
        "type": snap.get("type"),
        "runStatus": snap.get("runStatus"),
        "activeThreadCount": snap.get("activeThreadCount") or 0,
        "flowFilesIn": snap.get("flowFilesIn") or 0,
        "flowFilesOut": snap.get("flowFilesOut") or 0,
        "bytesRead": snap.get("bytesRead") or 0,
        "bytesWritten": snap.get("bytesWritten") or 0,
        "tasks": snap.get("tasks") or 0,
        # Human-readable strings NiFi builds itself; better than reformatting bytes here.
        "input": snap.get("input"),
        "output": snap.get("output"),
        "read": snap.get("read"),
        "written": snap.get("written"),
    }


def _connection_row(entry: dict[str, Any]) -> dict[str, Any]:
    snap = entry.get("connectionStatusSnapshot") or entry
    return {
        "id": snap.get("id"),
        "name": snap.get("name"),
        "sourceName": snap.get("sourceName"),
        "destinationName": snap.get("destinationName"),
        "flowFilesQueued": snap.get("flowFilesQueued") or 0,
        "bytesQueued": snap.get("bytesQueued") or 0,
        "flowFilesIn": snap.get("flowFilesIn") or 0,
        "flowFilesOut": snap.get("flowFilesOut") or 0,
        "queued": snap.get("queued"),
        "input": snap.get("input"),
        "output": snap.get("output"),
        # NiFi reports connection back-pressure predictions if configured.
        "percentUseCount": snap.get("percentUseCount") or 0,
        "percentUseBytes": snap.get("percentUseBytes") or 0,
    }


def _port_row(entry: dict[str, Any]) -> dict[str, Any]:
    snap = entry.get("portStatusSnapshot") or entry
    return {
        "id": snap.get("id"),
        "name": snap.get("name"),
        "runStatus": snap.get("runStatus"),
        "flowFilesIn": snap.get("flowFilesIn") or 0,
        "flowFilesOut": snap.get("flowFilesOut") or 0,
        "input": snap.get("input"),
        "output": snap.get("output"),
    }
