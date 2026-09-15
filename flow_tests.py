"""Post-deployment test suite for a NiFi flow.

A flow can deploy cleanly and still never run — an invalid property, a controller
service stuck ENABLING, or an unterminated relationship all fail silently. These
checks interrogate the deployed process group over the REST API and return a
pass/fail row per test case so the UI can show a verification table.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from nifi_client import NiFiClient

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"


def _case(
    case_id: str,
    name: str,
    status: str,
    description: str,
    details: str = "",
) -> dict[str, str]:
    return {
        "id": case_id,
        "name": name,
        "status": status,
        "description": description,
        "details": details,
    }


def extract_group_id(text: str) -> str | None:
    """Pull a processGroupId out of an agent's final answer."""
    match = re.search(
        r"processGroupId\"?\s*[:=]\s*\"?([0-9a-fA-F][0-9a-fA-F-]{7,})", text or ""
    )
    return match.group(1) if match else None


def find_group_id_by_name(client: NiFiClient, name: str) -> str | None:
    """Newest child group of root with this name."""
    if not name:
        return None
    root = client.root_process_group_id()
    data = client.get(f"/nifi-api/flow/process-groups/{root}") or {}
    groups = ((data.get("processGroupFlow") or {}).get("flow") or {}).get("processGroups") or []
    matches = [
        g for g in groups if ((g.get("component") or {}).get("name") or g.get("id")) == name
    ]
    if not matches:
        return None
    return matches[-1].get("id")


def _flow(client: NiFiClient, group_id: str) -> dict[str, Any]:
    data = client.get(f"/nifi-api/flow/process-groups/{group_id}") or {}
    return ((data.get("processGroupFlow") or {}).get("flow") or {})


def _all_processors(client: NiFiClient, group_id: str) -> list[dict[str, Any]]:
    """Processors in this group and, recursively, in its sub-groups."""
    flow = _flow(client, group_id)
    found = list(flow.get("processors") or [])
    for child in flow.get("processGroups") or []:
        child_id = child.get("id")
        if child_id:
            found.extend(_all_processors(client, child_id))
    return found


def _all_connections(client: NiFiClient, group_id: str) -> list[dict[str, Any]]:
    flow = _flow(client, group_id)
    found = list(flow.get("connections") or [])
    for child in flow.get("processGroups") or []:
        child_id = child.get("id")
        if child_id:
            found.extend(_all_connections(client, child_id))
    return found


def _expected_names(spec: dict[str, Any] | None) -> list[str]:
    if not spec:
        return []
    names: list[str] = []

    def walk(group: dict[str, Any]) -> None:
        for proc in group.get("processors") or []:
            if proc.get("name"):
                names.append(str(proc["name"]))
        for child in group.get("processGroups") or []:
            walk(child)

    walk(spec)
    return names


def _expected_connection_count(spec: dict[str, Any] | None) -> int:
    if not spec:
        return 0
    total = 0

    def walk(group: dict[str, Any]) -> None:
        nonlocal total
        total += len(group.get("connections") or [])
        for child in group.get("processGroups") or []:
            walk(child)

    walk(spec)
    return total


def _group_snapshot(client: NiFiClient, group_id: str) -> dict[str, Any]:
    data = client.get(f"/nifi-api/process-groups/{group_id}") or {}
    return ((data.get("status") or {}).get("aggregateSnapshot") or {})


def run_flow_tests(
    client: NiFiClient,
    group_id: str,
    spec: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
    started_at: float | None = None,
    wait_seconds: float = 0.0,
    golden: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a deployed process group. Never raises — a broken check is a failed row."""
    cases: list[dict[str, str]] = []
    expect_running = True if spec is None else bool(spec.get("start", True))

    # 1. The group itself
    group_name = ""
    try:
        group = client.get(f"/nifi-api/process-groups/{group_id}") or {}
        group_name = (group.get("component") or {}).get("name") or ""
        cases.append(
            _case(
                "group_created",
                "Process group created",
                PASS if group_name else FAIL,
                "The flow's process group exists on the NiFi canvas.",
                f"name={group_name or '(unknown)'} id={group_id}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        cases.append(
            _case(
                "group_created",
                "Process group created",
                FAIL,
                "The flow's process group exists on the NiFi canvas.",
                f"Could not read the group: {exc}",
            )
        )
        return _summarize(cases, group_id, group_name)

    # 2/3/4. Processors: created, valid, running
    processors: list[dict[str, Any]] = []
    try:
        processors = _all_processors(client, group_id)
        expected = _expected_names(spec)
        actual_names = [(p.get("component") or {}).get("name") or p.get("id") for p in processors]
        missing = [n for n in expected if n not in actual_names]
        cases.append(
            _case(
                "processors_created",
                "Processors created",
                FAIL if (missing or not processors) else PASS,
                "Every processor in the spec was created, including in sub-groups.",
                f"{len(processors)} created"
                + (f"; expected {len(expected)}" if expected else "")
                + (f"; MISSING {', '.join(missing)}" if missing else ""),
            )
        )

        invalid = []
        for proc in processors:
            component = proc.get("component") or {}
            errors = component.get("validationErrors") or []
            if errors:
                name = component.get("name") or proc.get("id")
                invalid.append(f"{name}: {'; '.join(str(e) for e in errors)[:160]}")
        cases.append(
            _case(
                "processors_valid",
                "Processor configuration valid",
                FAIL if invalid else PASS,
                "No processor reports a validation error (bad property, missing service).",
                " | ".join(invalid[:4]) if invalid else f"{len(processors)} processors valid",
            )
        )

        if not expect_running:
            cases.append(
                _case(
                    "processors_running",
                    "Processors running",
                    SKIP,
                    "Processors are in the RUNNING state.",
                    "Spec requested the group stay stopped (start=false).",
                )
            )
        else:
            not_running = []
            for proc in processors:
                state = (proc.get("component") or {}).get("state") or (
                    proc.get("status") or {}
                ).get("runStatus")
                if str(state).upper() not in {"RUNNING", "RUN"}:
                    name = (proc.get("component") or {}).get("name") or proc.get("id")
                    not_running.append(f"{name}={state}")
            cases.append(
                _case(
                    "processors_running",
                    "Processors running",
                    WARN if not_running else PASS,
                    "Processors are in the RUNNING state.",
                    ", ".join(not_running[:6]) if not_running else f"all {len(processors)} running",
                )
            )
    except Exception as exc:  # noqa: BLE001
        cases.append(
            _case("processors_created", "Processors created", FAIL,
                  "Every processor in the spec was created, including in sub-groups.",
                  f"Check failed: {exc}")
        )

    # 5. Connections
    try:
        connections = _all_connections(client, group_id)
        expected_conns = _expected_connection_count(spec)
        if expected_conns:
            status = FAIL if len(connections) < expected_conns else PASS
            details = f"{len(connections)} of {expected_conns} expected connections"
        elif len(processors) > 1 and not connections:
            # Several processors and nothing joining them means flow files go nowhere.
            status = WARN
            details = f"{len(processors)} processors but no connections between them"
        else:
            status = PASS
            details = f"{len(connections)} connections"
        cases.append(
            _case(
                "connections_created",
                "Connections wired",
                status,
                "Processors are connected, so flow files have a path through the flow.",
                details,
            )
        )
    except Exception as exc:  # noqa: BLE001
        cases.append(
            _case("connections_created", "Connections wired", FAIL,
                  "Processors are connected, so flow files have a path through the flow.",
                  f"Check failed: {exc}")
        )

    # 6. Controller services
    try:
        # Ancestor groups' services would otherwise be reported as this flow's own.
        data = (
            client.get(
                f"/nifi-api/flow/process-groups/{group_id}/controller-services"
                "?includeAncestorGroups=false&includeDescendantGroups=true"
            )
            or {}
        )
        scope = data.get("controllerServices") or []
        if not scope:
            cases.append(
                _case("services_enabled", "Controller services enabled", SKIP,
                      "Readers/writers the flow depends on are ENABLED and valid.",
                      "This flow declares no controller services.")
            )
        else:
            problems = []
            for svc in scope:
                component = svc.get("component") or {}
                name = component.get("name") or svc.get("id")
                state = str(component.get("state") or "").upper()
                errors = component.get("validationErrors") or []
                if state != "ENABLED":
                    problems.append(f"{name} state={state or 'unknown'}")
                if errors:
                    problems.append(f"{name}: {'; '.join(str(e) for e in errors)[:120]}")
            cases.append(
                _case(
                    "services_enabled",
                    "Controller services enabled",
                    FAIL if problems else PASS,
                    "Readers/writers the flow depends on are ENABLED and valid.",
                    " | ".join(problems[:4])
                    if problems
                    else ", ".join(
                        (s.get("component") or {}).get("name") or "" for s in scope
                    )[:160],
                )
            )
    except Exception as exc:  # noqa: BLE001
        cases.append(
            _case("services_enabled", "Controller services enabled", FAIL,
                  "Readers/writers the flow depends on are ENABLED and valid.",
                  f"Check failed: {exc}")
        )

    # 7/8/9. Runtime behaviour — give the flow a moment to actually move data.
    deadline = time.time() + max(0.0, wait_seconds)
    snapshot: dict[str, Any] = {}
    files_written: list[str] = []
    while True:
        try:
            snapshot = _group_snapshot(client, group_id)
        except Exception:  # noqa: BLE001
            snapshot = {}
        files_written = _new_output_files(output_dir, started_at)
        moved = _int(snapshot.get("flowFilesOut")) or _int(snapshot.get("flowFilesIn"))
        if files_written or moved or time.time() >= deadline:
            break
        time.sleep(1.5)

    try:
        bulletins = client.get(f"/nifi-api/flow/bulletin-board?groupId={group_id}") or {}
        entries = (bulletins.get("bulletinBoard") or {}).get("bulletins") or []
        errors, warns = [], []
        for entry in entries:
            bulletin = entry.get("bulletin") or {}
            level = str(bulletin.get("level") or "").upper()
            line = f"{bulletin.get('sourceName') or ''}: {str(bulletin.get('message') or '')[:140]}"
            if level == "ERROR":
                errors.append(line)
            elif level == "WARNING" or level == "WARN":
                warns.append(line)
        if errors:
            status, details = FAIL, " | ".join(errors[:3])
        elif warns:
            status, details = WARN, " | ".join(warns[:3])
        else:
            status, details = PASS, "No ERROR or WARNING bulletins for this group."
        cases.append(
            _case("no_bulletins", "No error bulletins", status,
                  "NiFi reported no runtime errors or warnings for this flow.", details)
        )
    except Exception as exc:  # noqa: BLE001
        cases.append(
            _case("no_bulletins", "No error bulletins", WARN,
                  "NiFi reported no runtime errors or warnings for this flow.",
                  f"Bulletin board unavailable: {exc}")
        )

    if not expect_running:
        cases.append(
            _case("data_flowed", "Data processed", SKIP,
                  "At least one flow file moved through the flow.",
                  "Group was deployed stopped, so no data is expected yet.")
        )
    else:
        out = _int(snapshot.get("flowFilesOut"))
        read = _int(snapshot.get("flowFilesIn"))
        queued = _int(snapshot.get("flowFilesQueued"))
        bytes_read = _int(snapshot.get("bytesRead")) or _int(snapshot.get("bytesWritten"))
        # A written output file is proof data flowed even when the windowed
        # counters have already rolled over.
        moved = out or read or queued or bytes_read or bool(files_written)
        evidence = f"in={read} out={out} queued={queued}"
        if bytes_read:
            evidence += f" bytes={bytes_read}"
        if files_written:
            evidence += f"; wrote {len(files_written)} file(s)"
        cases.append(
            _case(
                "data_flowed",
                "Data processed",
                PASS if moved else WARN,
                "At least one flow file moved through the flow.",
                evidence
                + ("" if moved else " — nothing to read yet, which is normal for an idle source"),
            )
        )
        cases.append(
            _case(
                "queue_drained",
                "No stuck queue",
                WARN if queued else PASS,
                "Nothing is piling up in a connection queue.",
                f"{queued} flow files queued" if queued else "all queues empty",
            )
        )

    if output_dir:
        cases.append(
            _case(
                "output_written",
                "Output file written",
                PASS if files_written else FAIL,
                "The flow wrote a real output file to the destination directory.",
                (", ".join(files_written[:3]) if files_written else f"no new files in {output_dir}"),
            )
        )

    if golden and output_dir:
        cases.append(_golden_case(output_dir, files_written, golden))

    return _summarize(cases, group_id, group_name)


def _golden_case(
    output_dir: str | Path,
    files_written: list[str],
    golden: dict[str, Any],
) -> dict[str, str]:
    """Compare the produced output against user-supplied expected contents.

    This closes the gap between "the flow deploys and writes something" and
    "the flow produces the *right* something" — a mis-configured Jolt spec or
    a wrong column pick can pass every structural test and still emit garbage.
    """
    expected_text = str(golden.get("contents") or "").strip("\ufeff")
    ignore_ws = bool(golden.get("ignoreTrailingWhitespace", True))
    if not expected_text:
        return _case(
            "output_matches_golden",
            "Output matches expected file",
            WARN,
            "Compares the flow's real output to a file you know is correct.",
            "No expected contents were provided.",
        )
    if not files_written:
        return _case(
            "output_matches_golden",
            "Output matches expected file",
            FAIL,
            "Compares the flow's real output to a file you know is correct.",
            "No output file was produced, so there is nothing to compare against.",
        )
    folder = Path(output_dir)
    # Compare against the newest matching file, since the flow may have written
    # more than one during the test window.
    candidate = _newest_file(folder, files_written)
    try:
        actual_text = candidate.read_text(encoding="utf-8").strip("\ufeff")
    except OSError as exc:
        return _case(
            "output_matches_golden",
            "Output matches expected file",
            FAIL,
            "Compares the flow's real output to a file you know is correct.",
            f"Could not read {candidate.name}: {exc}",
        )

    a = _canonicalise(actual_text, ignore_ws)
    b = _canonicalise(expected_text, ignore_ws)
    if a == b:
        return _case(
            "output_matches_golden",
            "Output matches expected file",
            PASS,
            "Compares the flow's real output to a file you know is correct.",
            f"{candidate.name} matches the {len(expected_text)}-char expected content.",
        )
    diff = _short_diff(actual_text, expected_text)
    return _case(
        "output_matches_golden",
        "Output matches expected file",
        FAIL,
        "Compares the flow's real output to a file you know is correct.",
        f"{candidate.name} differs from expected — {diff}",
    )


def _newest_file(folder: Path, names: list[str]) -> Path:
    paths = [folder / n for n in names if (folder / n).is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[0] if paths else (folder / names[0])


def _canonicalise(text: str, ignore_ws: bool) -> str:
    lines = text.splitlines()
    if ignore_ws:
        lines = [line.rstrip() for line in lines]
        while lines and not lines[-1]:
            lines.pop()
    return "\n".join(lines)


def _short_diff(actual: str, expected: str) -> str:
    """Return a compact line-anchored diff so the report card can render one row."""
    a_lines = actual.splitlines()
    b_lines = expected.splitlines()
    if len(a_lines) != len(b_lines):
        return f"line counts differ (got {len(a_lines)}, expected {len(b_lines)})"
    for i, (a, b) in enumerate(zip(a_lines, b_lines), start=1):
        if a != b:
            return f"first difference at line {i}: got {a!r}, expected {b!r}"
    return "content differs only in trailing whitespace"


def _new_output_files(output_dir: str | Path | None, started_at: float | None) -> list[str]:
    if not output_dir:
        return []
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    cutoff = (started_at or 0) - 2
    names = []
    for path in folder.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime >= cutoff:
                names.append(path.name)
        except OSError:
            continue
    return sorted(names)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summarize(
    cases: list[dict[str, str]], group_id: str, group_name: str
) -> dict[str, Any]:
    counts = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    return {
        "processGroupId": group_id,
        "processGroupName": group_name,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": counts[PASS],
            "failed": counts[FAIL],
            "warned": counts[WARN],
            "skipped": counts[SKIP],
            "ok": counts[FAIL] == 0,
        },
    }
