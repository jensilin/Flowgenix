"""Create a basic GenerateFlowFile -> LogAttribute flow on local NiFi."""

from __future__ import annotations

import sys

from nifi_client import NiFiClient


def main() -> int:
    client = NiFiClient()
    print("Logging in...")
    client.login()

    root_id = client.root_process_group_id()
    print(f"Root process group: {root_id}")

    pg = client.create_process_group(root_id, "cursor-basic-demo", x=100, y=100)
    pg_id = pg["id"]
    print(f"Created process group: cursor-basic-demo ({pg_id})")

    gen = client.create_processor(
        pg_id,
        name="GenerateFlowFile",
        type_name="org.apache.nifi.processors.standard.GenerateFlowFile",
        x=100,
        y=100,
        properties={
            "File Size": "1B",
            "Batch Size": "1",
            "Data Format": "Text",
            "Unique FlowFiles": "false",
            "Custom Text": "hello-from-cursor",
        },
        scheduling_period="5 sec",
    )
    gen_id = gen["id"]
    print(f"Created GenerateFlowFile ({gen_id})")

    # Refresh revision after create, then auto-terminate unused relationships later via LogAttribute side
    gen_fresh = client.get(f"/nifi-api/processors/{gen_id}")
    client.update_processor(
        gen_id,
        revision_version=gen_fresh["revision"]["version"],
        properties={
            "File Size": "1B",
            "Batch Size": "1",
            "Data Format": "Text",
            "Unique FlowFiles": "false",
            "Custom Text": "hello-from-cursor",
        },
        scheduling_period="5 sec",
    )

    log = client.create_processor(
        pg_id,
        name="LogAttribute",
        type_name="org.apache.nifi.processors.standard.LogAttribute",
        x=400,
        y=100,
        properties={
            "Log Level": "info",
            "Log Payload": "true",
            "Attributes to Log": "",
        },
    )
    log_id = log["id"]
    print(f"Created LogAttribute ({log_id})")

    log_fresh = client.get(f"/nifi-api/processors/{log_id}")
    client.update_processor(
        log_id,
        revision_version=log_fresh["revision"]["version"],
        auto_terminated=["success"],
        properties={
            "Log Level": "info",
            "Log Payload": "true",
        },
    )

    conn = client.create_connection(
        pg_id,
        source_id=gen_id,
        destination_id=log_id,
        relationships=["success"],
        name="success",
    )
    print(f"Connected processors ({conn['id']})")

    client.start_process_group(pg_id)
    print("Started process group (RUNNING)")
    print()
    print("Open NiFi UI and double-click process group 'cursor-basic-demo':")
    print("  https://127.0.0.1:8443/nifi/")
    print("FlowFiles should appear every ~5 seconds; check bulletins / nifi-app.log for payload.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - surface clear CLI errors
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
