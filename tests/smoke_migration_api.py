"""Manual end-to-end check of the migration HTTP endpoints against a running server.

Not part of the pytest suite (no `test_` prefix): it needs a live Flow Studio on
FLOW_STUDIO_PORT. Run it after starting the server to confirm the wiring between
the browser, the endpoints, and the engine.

    python tests/smoke_migration_api.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = f"http://127.0.0.1:{os.getenv('FLOW_STUDIO_PORT', '7871')}"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def post(path: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def sse_events(body: str) -> list[dict]:
    out = []
    for chunk in body.split("\n\n"):
        line = chunk.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


def main() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    # 1. Version registry
    status, body = get("/api/migration/versions")
    data = json.loads(body) if status == 200 else {}
    versions = [v["version"] for v in data.get("versions", [])]
    check("GET /api/migration/versions", status == 200 and len(versions) > 5, f"{len(versions)} versions")
    check("newest version is offered first", versions[:1] == ["2.11.0"] if versions else False, str(versions[:3]))

    # 2. Inspect an XML template
    xml_text = (FIXTURES / "template_1x.xml").read_text(encoding="utf-8")
    status, body = post(
        "/api/migration/inspect", {"source_text": xml_text, "source_filename": "legacy.xml"}
    )
    info = json.loads(body) if status == 200 else {}
    check("inspect XML template", status == 200, info.get("sourceFormat", body[:120]))
    check("XML source version detected", info.get("detectedVersion") == "1.25.0", str(info.get("detectedVersion")))
    check("XML components counted", info.get("totalComponents") == 9, str(info.get("totalComponents")))

    # 3. A .json upload from NiFi 1.x must NOT be reported as 2.x
    json_1x = (FIXTURES / "flow_1x.json").read_text(encoding="utf-8")
    status, body = post(
        "/api/migration/inspect", {"source_text": json_1x, "source_filename": "legacy.json"}
    )
    info = json.loads(body) if status == 200 else {}
    check(
        "1.x JSON is detected as 1.x, not 2.x",
        info.get("detectedVersion", "").startswith("1."),
        str(info.get("detectedVersion")),
    )

    # 4. An ambiguous file reports unknown instead of guessing
    ambiguous = (FIXTURES / "flow_unversioned.json").read_text(encoding="utf-8")
    status, body = post(
        "/api/migration/inspect", {"source_text": ambiguous, "source_filename": "x.json"}
    )
    info = json.loads(body) if status == 200 else {}
    check(
        "ambiguous file reports unknown",
        info.get("detectedVersion") is None and info.get("detectionConfidence") == "unknown",
        str(info.get("detectionConfidence")),
    )

    # 5. Garbage upload is rejected with a helpful message
    status, body = post("/api/migration/inspect", {"source_text": "id,name\n1,a\n"})
    check("non-flow upload rejected with 400", status == 400, body[:120])

    # 6. Deterministic analysis (no AI, no NiFi)
    status, body = post(
        "/api/migration/analyze",
        {
            "source_text": xml_text,
            "source_filename": "legacy.xml",
            "source_version": "1.25.0",
            "target_version": "2.11.0",
            "use_ai": False,
        },
    )
    events = sse_events(body) if status == 200 else []
    analysis = next((e["analysis"] for e in events if e.get("type") == "analysis"), None)
    check("analyze streams an analysis", analysis is not None, f"{len(events)} events")
    if analysis:
        s = analysis["summary"]
        check("summary totals add up", sum(s["byOutcome"].values()) == s["totalComponents"], str(s["totalComponents"]))
        check("GetHTTP removal detected", s["removed"] >= 1, f"removed={s['removed']}")
        check("template concern raised", any(c["key"] == "templates_removed" for c in analysis["concerns"]))
        check("variable warning raised", any("Variable Registry" in w for w in analysis["warnings"]))
    check("no error event", not any(e.get("type") == "error" for e in events))

    # 7. Missing source version is refused, not guessed
    status, body = post(
        "/api/migration/analyze",
        {"source_text": ambiguous, "source_filename": "x.json", "target_version": "2.11.0", "use_ai": False},
    )
    events = sse_events(body)
    err = next((e for e in events if e.get("type") == "error"), None)
    check("undetectable version is refused", err is not None and "could not be detected" in err["message"], str(err))

    # 8. Generate the migrated flow + reports
    status, body = post(
        "/api/migration/generate",
        {
            "source_text": xml_text,
            "source_filename": "legacy.xml",
            "source_version": "1.25.0",
            "target_version": "2.11.0",
            "use_ai": False,
        },
    )
    events = sse_events(body) if status == 200 else []
    generated = next((e for e in events if e.get("type") == "generated"), None)
    check("generate emits artifacts", generated is not None, f"{len(events)} events")

    if generated:
        artifacts = generated["artifacts"]
        gen = generated["generation"]
        check("changes were applied", gen["appliedCount"] > 0, f"applied={gen['appliedCount']}")
        check("unsafe components were skipped", gen["skippedCount"] > 0, f"skipped={gen['skippedCount']}")

        for key, label in (
            ("migratedFlow", "migrated flow"),
            ("reportMarkdown", "markdown report"),
            ("reportJson", "json report"),
        ):
            status, content = get(artifacts[key]["downloadUrl"])
            check(f"download {label}", status == 200 and len(content) > 100, f"{len(content)} bytes")

        status, flow_text = get(artifacts["migratedFlow"]["downloadUrl"])
        flow = json.loads(flow_text)
        procs = flow["flowContents"]["processors"]
        reshape = next(p for p in procs if p["name"] == "Reshape Records")
        check("Jolt property renamed in output", "Jolt Transform" in reshape["properties"])
        check("EVENT_DRIVEN rewritten in output", reshape["schedulingStrategy"] == "TIMER_DRIVEN")
        fetch = next(p for p in procs if p["name"] == "Fetch Feed")
        check("removed processor left unchanged", fetch["type"].endswith("GetHTTP"))
        check("removed processor marked in output", "MANUAL REVIEW REQUIRED" in fetch.get("comments", ""))
        check("provenance stamped", flow.get("flowStudioMigration", {}).get("targetVersion") == "2.11.0")

        status, md = get(artifacts["reportMarkdown"]["downloadUrl"])
        check("report names both versions", "1.25.0" in md and "2.11.0" in md)
        check("report has a manual-review section", "## Manual intervention required" in md)

    # 9. Path traversal on the artifact download is refused
    status, _ = get("/api/migration/download?name=../pytest.ini")
    check("traversal refused", status == 400, str(status))
    status, _ = get("/api/migration/download?name=nope.json")
    check("missing artifact 404s", status == 404, str(status))

    # 10. Existing endpoints still respond
    status, _ = get("/api/flows")
    check("existing /api/flows still works", status == 200)
    status, _ = get("/")
    check("existing index still works", status == 200)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All migration API checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
