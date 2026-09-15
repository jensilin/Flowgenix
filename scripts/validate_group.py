"""Validate a deployed NiFi process group."""
from __future__ import annotations

import sys

from nifi_client import NiFiClient


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_group.py <group-id>")
        return 2
    gid = sys.argv[1]
    c = NiFiClient()
    c.login()

    flow = c.get(f"/nifi-api/flow/process-groups/{gid}") or {}
    processors = ((flow.get("processGroupFlow") or {}).get("flow") or {}).get("processors") or []
    print("=== PROCESSORS ===")
    all_valid = True
    for p in processors:
        comp = p.get("component") or {}
        name = comp.get("name")
        state = comp.get("state")
        errs = comp.get("validationErrors") or []
        if errs:
            all_valid = False
        print(f"{name}: state={state} errors={errs or 'none'}")

    print("\n=== CONTROLLER SERVICES ===")
    cs = c.get(f"/nifi-api/flow/process-groups/{gid}/controller-services") or {}
    svc_ok = True
    for svc in cs.get("controllerServices") or []:
        comp = svc.get("component") or {}
        name = comp.get("name")
        if name not in ("PmCsvReader", "PmJsonWriter", "NbiSslContext"):
            continue
        state = comp.get("state")
        errs = comp.get("validationErrors") or []
        if state != "ENABLED" or errs:
            svc_ok = False
        print(f"{name}: state={state} errors={errs or 'none'}")

    print("\n=== BULLETINS (WARN/ERROR) ===")
    bb = c.get(f"/nifi-api/flow/bulletin-board?groupId={gid}") or {}
    entries = (bb.get("bulletinBoard") or {}).get("bulletins") or []
    warn_err = [
        b
        for b in entries
        if (b.get("bulletin") or {}).get("level") in ("WARN", "ERROR", "WARNING")
    ]
    if not warn_err:
        print("None")
    else:
        for b in warn_err[:20]:
            bl = b.get("bulletin") or {}
            print(f"{bl.get('level')}: {bl.get('sourceName')} - {bl.get('message')}")

    run_status = (
        ((flow.get("processGroupFlow") or {}).get("flow") or {})
        .get("processGroupStatus", {})
        .get("aggregateSnapshot", {})
        .get("runStatus")
    )
    print("\n=== SUMMARY ===")
    print(f"processors_valid: {all_valid}")
    print(f"services_ok: {svc_ok}")
    print(f"group_run_status: {run_status}")
    return 0 if all_valid and svc_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
