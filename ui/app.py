"""Flowgenix: offline NiFi flow migration (XML ↔ JSON, version-aware)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
MIGRATIONS_DIR = REPO_ROOT / "migrations"
_MIGRATION_EXTENSIONS = {".json", ".md", ".xml"}

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
    artifacts = _write_migration_artifacts(analysis, generation, body.output_name)
    emit(
        {
            "type": "generated",
            "generation": generation.to_dict(),
            "artifacts": artifacts,
            "analysis": analysis.to_dict(),
        }
    )


def _write_migration_artifacts(
    analysis: Any,
    generation: Any,
    output_name: str | None,
) -> dict[str, Any]:
    from flow_document import FORMAT_XML_TEMPLATE
    from migration_engine import build_report, migrated_json_text, migrated_xml_text, render_report_markdown

    MIGRATIONS_DIR.mkdir(exist_ok=True)
    stem = _safe_migration_stem(
        output_name or f"{Path(analysis.filename).stem}-nifi-{analysis.target_version}"
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{stem}"

    # NiFi names an imported process group after the file it was uploaded from,
    # so the flow is served under its own name rather than the stamped one we
    # keep on disk to avoid collisions.
    flow_stem = _safe_migration_stem(_root_group_name(generation) or stem)

    flow_links: dict[str, Any] = {}
    if generation.output_format == FORMAT_XML_TEMPLATE and generation.xml_text:
        xml_path = MIGRATIONS_DIR / f"{base}.migrated.xml"
        xml_path.write_text(migrated_xml_text(generation), encoding="utf-8")
        flow_links["migratedXml"] = _artifact_link(xml_path, f"{flow_stem}.xml")
    else:
        json_path = MIGRATIONS_DIR / f"{base}.migrated.json"
        json_path.write_text(migrated_json_text(generation), encoding="utf-8")
        flow_links["migratedFlow"] = _artifact_link(json_path, f"{flow_stem}.json")

    report = build_report(analysis, generation)
    report_json_path = MIGRATIONS_DIR / f"{base}.report.json"
    report_json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report_md_path = MIGRATIONS_DIR / f"{base}.report.md"
    report_md_path.write_text(render_report_markdown(report), encoding="utf-8")

    return {
        **flow_links,
        "reportJson": _artifact_link(report_json_path),
        "reportMarkdown": _artifact_link(report_md_path),
        "report": report,
    }


def _artifact_link(path: Path, download_name: str | None = None) -> dict[str, Any]:
    url = f"/api/migration/download?name={path.name}"
    if download_name and download_name != path.name:
        url += f"&as={download_name}"
    return {
        "name": download_name or path.name,
        "size": path.stat().st_size,
        "downloadUrl": url,
    }


def _root_group_name(generation: Any) -> str:
    migrated = getattr(generation, "migrated", None)
    if not isinstance(migrated, dict):
        return ""
    root = migrated.get("flowContents") or migrated.get("rootGroup") or migrated
    return (root.get("name") or "").strip() if isinstance(root, dict) else ""


@app.get("/api/migration/download")
async def download_migration(
    name: str, as_name: str | None = Query(default=None, alias="as")
) -> FileResponse:
    safe = _safe_migration_name(name)
    path = (MIGRATIONS_DIR / safe).resolve()
    if not str(path).startswith(str(MIGRATIONS_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="Migration artifact not found")
    media = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".xml": "application/xml",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=_safe_migration_name(as_name or path.name))


def _safe_migration_name(name: str) -> str:
    cleaned = (name or "").replace("\\", "/").lstrip("/")
    if "/" in cleaned or ".." in cleaned:
        raise HTTPException(status_code=400, detail="Invalid artifact name")
    if Path(cleaned).suffix.lower() not in _MIGRATION_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(sorted(_MIGRATION_EXTENSIONS))} artifacts are served",
        )
    return cleaned


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
