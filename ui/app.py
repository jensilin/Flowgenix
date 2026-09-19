"""Flowgenix: offline NiFi flow migration (XML ↔ JSON, version-aware)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Migrated files are handed back inside the response and saved by the browser,
#: so a request never depends on a file written by an earlier one. Point this at
#: a writable directory to also keep a copy on the server; leave it unset on
#: serverless hosts, where the filesystem is read-only and per-invocation.
ARCHIVE_DIR = os.getenv("FLOWGENIX_ARCHIVE_DIR", "").strip()

app = FastAPI(title="Flowgenix")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class MigrationInspectRequest(BaseModel):
    source_text: str = Field(min_length=2)
    source_filename: str = "flow.xml"


class MigrationAnalyzeRequest(BaseModel):
    source_text: str = Field(min_length=2)
    source_filename: str = "flow.xml"
    source_version: str | None = None
    target_version: str = Field(min_length=3)
    output_format: str = "json_snapshot"


class MigrationGenerateRequest(MigrationAnalyzeRequest):
    output_name: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/migration/versions")
async def migration_versions() -> dict[str, Any]:
    from migration_rules import rule_summary
    from nifi_versions import version_choices

    return {"versions": version_choices(), "rules": rule_summary()}


@app.post("/api/migration/inspect")
async def migration_inspect(body: MigrationInspectRequest) -> dict[str, Any]:
    from flow_document import FlowParseError, detected_line, parse_flow_file

    try:
        doc = await asyncio.to_thread(
            parse_flow_file, body.source_text, body.source_filename or "flow.xml"
        )
    except FlowParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not parse the flow file: {exc}") from exc

    summary = doc.summary()
    summary["detectedLine"] = detected_line(doc)
    summary["processorTypes"] = sorted({c.short_type for c in doc.processors if c.short_type})
    summary["serviceTypes"] = sorted(
        {c.short_type for c in doc.controller_services if c.short_type}
    )
    return summary


@app.post("/api/migration/analyze")
async def migration_analyze(body: MigrationAnalyzeRequest) -> StreamingResponse:
    return StreamingResponse(_migration_stream(body, generate=False), media_type="text/event-stream")


@app.post("/api/migration/generate")
async def migration_generate(body: MigrationGenerateRequest) -> StreamingResponse:
    return StreamingResponse(_migration_stream(body, generate=True), media_type="text/event-stream")


async def _migration_stream(body: MigrationAnalyzeRequest, generate: bool) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False)
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    async def runner() -> None:
        try:
            await asyncio.to_thread(_run_migration_sync, body, emit, generate)
            emit({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "message": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(runner())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {item}\n\n".encode("utf-8")
    finally:
        await task


def _run_migration_sync(
    body: MigrationAnalyzeRequest,
    emit: Any,
    generate: bool,
) -> None:
    from flow_document import (
        FORMAT_JSON_SNAPSHOT,
        FORMAT_XML_TEMPLATE,
        FlowParseError,
        detected_line,
        parse_flow_file,
    )
    from migration_engine import analyze_migration, generate_migrated_flow
    from nifi_versions import get_version, resolve_or_raise

    emit({"type": "status", "message": f"Parsing {body.source_filename}…"})
    try:
        doc = parse_flow_file(body.source_text, body.source_filename or "flow.xml")
    except FlowParseError as exc:
        raise RuntimeError(str(exc)) from exc

    source_version = (body.source_version or "").strip() or doc.detected_version
    if not source_version:
        line = detected_line(doc)
        hint = f" The file looks like NiFi {line}." if line else ""
        raise RuntimeError(
            "Source NiFi version could not be detected from the file and none was "
            f"selected.{hint} Choose the source version and run the analysis again."
        )
    resolve_or_raise(source_version, "source")
    target = resolve_or_raise(body.target_version, "target")

    requested = (body.output_format or FORMAT_JSON_SNAPSHOT).strip()
    if requested == FORMAT_XML_TEMPLATE:
        ver = get_version(target.version)
        if ver is not None and not ver.supports_templates:
            raise RuntimeError(
                f"NiFi {target.version} cannot import XML templates (removed in 2.0). "
                "Choose JSON output, or pick a 1.x target."
            )

    emit(
        {
            "type": "status",
            "message": (
                f"Analysing {doc.summary()['totalComponents']} component(s) for "
                f"NiFi {source_version} → {target.version}."
            ),
        }
    )

    analysis = analyze_migration(doc, source_version, target.version)
    emit({"type": "analysis", "analysis": analysis.to_dict()})

    if not generate:
        return

    emit({"type": "status", "message": "Generating the migrated flow…"})
    generation = generate_migrated_flow(doc, analysis, output_format=requested)
    artifacts = _build_migration_artifacts(analysis, generation, body.output_name)
    emit(
        {
            "type": "generated",
            "generation": generation.to_dict(),
            "artifacts": artifacts,
            "analysis": analysis.to_dict(),
        }
    )


def _build_migration_artifacts(
    analysis: Any,
    generation: Any,
    output_name: str | None,
) -> dict[str, Any]:
    """Build every downloadable file for one migration, contents included.

    Nothing is kept on the server: the browser turns the text below into
    downloads itself, which is what lets Flowgenix run on a host with no disk.
    """
    from flow_document import FORMAT_XML_TEMPLATE
    from migration_engine import build_report, migrated_json_text, migrated_xml_text, render_report_markdown

    # NiFi names an imported process group after the file it was uploaded from,
    # so the flow is named after the group inside it.
    stem = _safe_migration_stem(
        output_name
        or _root_group_name(generation)
        or f"{Path(analysis.filename).stem}-nifi-{analysis.target_version}"
    )
    report = build_report(analysis, generation)

    artifacts: dict[str, Any] = {}
    if generation.output_format == FORMAT_XML_TEMPLATE and generation.xml_text:
        artifacts["migratedXml"] = _artifact(
            f"{stem}.xml", "application/xml", migrated_xml_text(generation)
        )
    else:
        artifacts["migratedFlow"] = _artifact(
            f"{stem}.json", "application/json", migrated_json_text(generation)
        )
    artifacts["reportMarkdown"] = _artifact(
        f"{stem}-report.md", "text/markdown", render_report_markdown(report)
    )
    # The JSON report is the report object itself. The browser serialises it for
    # download rather than carrying a second copy of it over the wire.
    artifacts["report"] = report

    _archive_migration(stem, artifacts, report)
    return artifacts


def _artifact(name: str, media_type: str, text: str) -> dict[str, Any]:
    return {
        "name": name,
        "mediaType": media_type,
        "size": len(text.encode("utf-8")),
        "text": text,
    }


def _archive_migration(stem: str, artifacts: dict[str, Any], report: dict[str, Any]) -> None:
    if not ARCHIVE_DIR:
        return
    directory = Path(ARCHIVE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}"
    for key in ("migratedFlow", "migratedXml", "reportMarkdown"):
        item = artifacts.get(key)
        if item:
            suffix = Path(item["name"]).name[len(stem) :]
            (directory / f"{base}{suffix}").write_text(item["text"], encoding="utf-8")
    (directory / f"{base}-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _root_group_name(generation: Any) -> str:
    migrated = getattr(generation, "migrated", None)
    if not isinstance(migrated, dict):
        return ""
    root = migrated.get("flowContents") or migrated.get("rootGroup") or migrated
    return (root.get("name") or "").strip() if isinstance(root, dict) else ""


def _safe_migration_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-._")
    return (re.sub(r"\.{2,}", ".", stem) or "migrated-flow")[:80]


def main() -> None:
    import uvicorn

    os.chdir(REPO_ROOT)
    host = os.getenv("FLOWGENIX_HOST") or os.getenv("FLOW_STUDIO_HOST", "127.0.0.1")
    port = int(os.getenv("FLOWGENIX_PORT") or os.getenv("FLOW_STUDIO_PORT", "7860"))
    reload = os.getenv("FLOWGENIX_RELOAD", os.getenv("FLOW_STUDIO_RELOAD", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    uvicorn.run("ui.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
