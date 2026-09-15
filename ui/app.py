"""FastAPI UI: Cursor API key + model + prompt → create NiFi flow via local SDK agent."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .win_bridge_patch import apply as apply_win_bridge_patch

apply_win_bridge_patch()

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOWS_DIR = REPO_ROOT / "flows"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="NiFi Flow Studio")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ModelsRequest(BaseModel):
    api_key: str = Field(min_length=8)


class NiFiAuth(BaseModel):
    nifi_url: str = Field(min_length=8)
    # Blank is allowed: an instance with authentication disabled has no credentials to
    # give. NiFiClient.login() rejects blanks when the instance does require them.
    nifi_username: str = ""
    nifi_password: str = ""


class CreateFlowRequest(NiFiAuth):
    api_key: str = Field(min_length=8)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=3)
    source_json: str | None = None
    source_filename: str | None = None
    # None means "every inferred column"; a list restricts the CSV to those fields.
    selected_columns: list[str] | None = None


class AnalyzeJsonRequest(NiFiAuth):
    source_json: str = Field(min_length=2)
    source_filename: str = "upload.json"
    selected_columns: list[str] | None = None


class ChatTurn(BaseModel):
    role: str = "user"
    text: str = ""


class PromptAssistRequest(BaseModel):
    """Chat bot request. NiFi auth is optional — without it the bot still answers,
    but it cannot pin its suggestions to the installed processor catalog."""

    api_key: str = Field(min_length=8)
    model: str = Field(min_length=1)
    scenario: str = Field(min_length=3)
    history: list[ChatTurn] = Field(default_factory=list)
    nifi_url: str | None = None
    nifi_username: str | None = None
    nifi_password: str | None = None
    source_json: str | None = None
    source_filename: str | None = None
    selected_columns: list[str] | None = None


class CreateJsonToCsvRequest(NiFiAuth):
    source_json: str = Field(min_length=2)
    source_filename: str = "upload.json"
    process_group_name: str | None = None
    selected_columns: list[str] | None = None
    # When true the endpoint stops after building the spec and returns a deploy
    # plan; the client is expected to POST /api/flows/deploy to apply it.
    dry_run: bool = False
    # Optional golden output: after deploy, the tests compare the produced CSV
    # against this content. `filename` is decorative; `contents` is what the
    # test compares against.
    expected_output: dict[str, Any] | None = None


class DeployFlowRequest(NiFiAuth):
    spec_path: str = Field(min_length=3)
    # Optional in-flight edits to the stored spec, e.g. renaming the group or
    # flipping `replaceExisting`. Merged into the loaded spec before applying.
    overrides: dict[str, Any] | None = None


class FlowStatusRequest(NiFiAuth):
    group_id: str = Field(min_length=8)


class FlowLifecycleRequest(FlowStatusRequest):
    action: str = Field(min_length=1)  # "start" | "stop" | "delete"


class PlanFlowRequest(NiFiAuth):
    # Either a stored spec path or an inline spec object. `spec_path` wins if both.
    spec_path: str | None = None
    spec: dict[str, Any] | None = None


class MigrationInspectRequest(BaseModel):
    """Parse an uploaded NiFi export and report its format + detected version.

    NiFi auth is deliberately absent: inspecting a file is a pure parse and must
    work for someone who has neither the source nor the target instance running.
    """

    source_text: str = Field(min_length=2)
    source_filename: str = "flow.xml"


class MigrationAnalyzeRequest(BaseModel):
    """Analyse an uploaded flow against a target version.

    Everything except the file and the target version is optional:
      * `source_version` — omitted when detection succeeded; required when it did not.
      * `api_key`/`model` — omitted to run a deterministic-only analysis.
      * `nifi_*` — when the connected instance runs the target version, its live
        catalog becomes the authority for component availability.
    """

    source_text: str = Field(min_length=2)
    source_filename: str = "flow.xml"
    source_version: str | None = None
    target_version: str = Field(min_length=3)
    api_key: str | None = None
    model: str | None = None
    use_ai: bool = True
    nifi_url: str | None = None
    nifi_username: str | None = None
    nifi_password: str | None = None


class MigrationGenerateRequest(MigrationAnalyzeRequest):
    """Generate the migrated flow. Re-runs the analysis so the artifact can never
    drift from the report shown in the UI."""

    output_name: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/models")
async def list_models(body: ModelsRequest) -> dict[str, Any]:
    api_key = body.api_key.strip()
    try:
        models = await asyncio.to_thread(_list_models_sync, api_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": models}


@app.get("/api/usage")
async def get_usage() -> dict[str, Any]:
    from usage_tracker import summary

    return summary()


@app.post("/api/usage/reset")
async def reset_usage() -> dict[str, Any]:
    from usage_tracker import reset, summary

    reset()
    return summary()


@app.get("/api/flows")
async def list_flows() -> dict[str, Any]:
    FLOWS_DIR.mkdir(exist_ok=True)
    files = []
    for path in FLOWS_DIR.rglob("*.json"):
        if path.name.startswith("."):
            continue
        rel = path.relative_to(FLOWS_DIR).as_posix()
        files.append(
            {
                "name": rel,
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime,
                "downloadUrl": f"/api/flows/download?name={rel}",
            }
        )
    files.sort(key=lambda item: item["mtime"], reverse=True)
    return {"flows": files}


@app.get("/api/flows/download")
async def download_flow(name: str) -> FileResponse:
    safe = _safe_flow_name(name)
    path = (FLOWS_DIR / safe).resolve()
    if not str(path).startswith(str(FLOWS_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="Flow JSON not found")
    return FileResponse(
        path,
        media_type="application/json",
        filename=path.name,
    )


def _safe_flow_name(name: str) -> str:
    cleaned = name.replace("\\", "/").lstrip("/")
    if ".." in cleaned.split("/") or cleaned.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid flow name")
    if not cleaned.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json flow specs are allowed")
    return cleaned


def _list_models_sync(api_key: str) -> list[dict[str, str]]:
    from cursor_sdk import Cursor

    models = Cursor.models.list(api_key=api_key)
    out: list[dict[str, str]] = []
    for m in models:
        model_id = getattr(m, "id", None) or str(m)
        display = getattr(m, "display_name", None) or getattr(m, "name", None) or model_id
        out.append({"id": model_id, "displayName": str(display)})
    preferred = {"composer-2.5", "auto", "auto-smart"}
    out.sort(key=lambda x: (0 if x["id"] in preferred else 1, x["displayName"].lower()))
    return out


def _apply_nifi_env(url: str, username: str, password: str) -> str:
    from nifi_client import NiFiClient

    if not url.strip().startswith("http"):
        raise HTTPException(status_code=400, detail="NiFi URL must start with http:// or https://")
    # People paste the UI address ("…/nifi/" on 1.x, "…/nf/" on 2.x); the REST API lives
    # at the server root, and the UI single-page app answers unknown paths with HTTP 200
    # and an HTML page, so an unreduced URL fails in a very confusing way.
    nifi_url = NiFiClient._normalize_base_url(url)
    os.environ["NIFI_URL"] = nifi_url
    os.environ["NIFI_USERNAME"] = username.strip()
    os.environ["NIFI_PASSWORD"] = password
    return nifi_url


def _inspect_nifi_sync() -> dict[str, Any]:
    from nifi_catalog import load_catalog

    catalog = load_catalog()
    return catalog.summary()


@app.post("/api/nifi/inspect")
async def inspect_nifi(body: NiFiAuth) -> dict[str, Any]:
    _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        return await asyncio.to_thread(_inspect_nifi_sync)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/json/analyze")
async def analyze_json_upload(body: AnalyzeJsonRequest) -> dict[str, Any]:
    _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        from json_to_csv import analyze_upload
        from nifi_catalog import load_catalog

        def _run() -> dict[str, Any]:
                catalog = load_catalog()
                analysis = analyze_upload(
                    body.source_json,
                    body.source_filename or "upload.json",
                    selected_columns=body.selected_columns,
                )
                return {
                    "nifi": catalog.summary(),
                "analysis": {
                    "filename": analysis["filename"],
                    "shape": analysis["shape"],
                    "nestedField": analysis["nestedField"],
                    "recordCount": analysis["recordCount"],
                    "columns": analysis["columns"],
                    "truncated": analysis["truncated"],
                    "parseNotes": analysis.get("parseNotes") or [],
                    "droppedColumns": analysis.get("droppedColumns") or [],
                },
            }

        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/json-to-csv")
async def create_json_to_csv(body: CreateJsonToCsvRequest) -> dict[str, Any]:
    nifi_url = _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        from build_flow_from_spec import build_from_spec
        from json_to_csv import build_from_upload
        from nifi_catalog import load_catalog
        from nifi_client import NiFiClient

        def _run() -> dict[str, Any]:
            # The client is shared so the spec builder can ask this NiFi which
            # directories it can actually see before pinning GetFile/PutFile paths.
            client = NiFiClient()
            client.login()
            catalog = load_catalog(client)
            spec, spec_path = build_from_upload(
                body.source_json,
                body.source_filename or "upload.json",
                catalog=catalog,
                selected_columns=body.selected_columns,
                client=client,
            )
            if body.process_group_name:
                spec["processGroupName"] = body.process_group_name
                spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            rel = spec_path.relative_to(FLOWS_DIR).as_posix()

            if body.dry_run:
                from flow_plan import build_plan

                return {
                    "dryRun": True,
                    "nifi": catalog.summary(),
                    "analysis": spec.get("sourceAnalysis"),
                    "specPath": rel,
                    "spec": spec,
                    "plan": build_plan(spec, client, catalog),
                    "downloadUrl": f"/api/flows/download?name={rel}",
                    "ui": f"{nifi_url}/nifi/",
                }

            started = time.time()
            result = build_from_spec(spec, client=client)
            path_note = (spec.get("sourceAnalysis") or {}).get("pathNote")
            if path_note:
                result.setdefault("warnings", []).append(path_note)
            return {
                "nifi": catalog.summary(),
                "analysis": spec.get("sourceAnalysis"),
                "specPath": rel,
                "spec": spec,
                "result": result,
                "tests": _test_json_to_csv_flow(
                    result, spec, started, client=client, golden=body.expected_output,
                ),
                "downloadUrl": f"/api/flows/download?name={rel}",
                "ui": f"{nifi_url}/nifi/",
            }

        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/flows/plan")
async def plan_flow(body: PlanFlowRequest) -> dict[str, Any]:
    """Return a deploy plan for a spec without touching NiFi state.

    Called by the review-before-deploy panel: the client sends either the
    on-disk spec path (from a previous build) or an inline spec object.
    """
    nifi_url = _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        from flow_plan import build_plan
        from nifi_catalog import load_catalog
        from nifi_client import NiFiClient

        def _run() -> dict[str, Any]:
            spec = _load_spec(body.spec_path, body.spec)
            client = NiFiClient()
            client.login()
            catalog = load_catalog(client)
            return {
                "nifi": catalog.summary(),
                "specPath": body.spec_path,
                "spec": spec,
                "plan": build_plan(spec, client, catalog),
                "ui": f"{nifi_url}/nifi/",
            }

        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/flows/deploy")
async def deploy_flow(body: DeployFlowRequest) -> dict[str, Any]:
    """Apply a previously built spec to NiFi.

    Complements /api/flows/plan: after the user reviews the plan they POST here
    with the same spec path (plus any last-minute overrides) to actually create
    the flow.
    """
    nifi_url = _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        from build_flow_from_spec import build_from_spec
        from nifi_catalog import load_catalog
        from nifi_client import NiFiClient

        def _run() -> dict[str, Any]:
            spec = _load_spec(body.spec_path, None)
            for key, value in (body.overrides or {}).items():
                spec[key] = value
            client = NiFiClient()
            client.login()
            catalog = load_catalog(client)
            started = time.time()
            result = build_from_spec(spec, client=client)
            path_note = (spec.get("sourceAnalysis") or {}).get("pathNote")
            if path_note:
                result.setdefault("warnings", []).append(path_note)
            return {
                "nifi": catalog.summary(),
                "specPath": body.spec_path,
                "spec": spec,
                "result": result,
                "tests": _test_json_to_csv_flow(result, spec, started, client=client),
                "downloadUrl": f"/api/flows/download?name={body.spec_path}",
                "ui": f"{nifi_url}/nifi/",
            }

        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/flows/status")
async def flow_status(body: FlowStatusRequest) -> dict[str, Any]:
    """Return a live status snapshot for a deployed process group.

    The observability panel polls this every ~2s. It is a single-request
    aggregation of NiFi's status + bulletin endpoints, so the client stays
    simple and one poll interval == one HTTP round-trip.
    """
    _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    try:
        from flow_status import snapshot
        from nifi_client import NiFiClient

        def _run() -> dict[str, Any]:
            client = NiFiClient()
            client.login()
            return snapshot(client, body.group_id)

        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/flows/lifecycle")
async def flow_lifecycle(body: FlowLifecycleRequest) -> dict[str, Any]:
    """Start, stop, or delete a deployed process group.

    Complements the status endpoint: the observability panel offers Start /
    Stop / Delete buttons so the user can act on what they see without leaving
    Flow Studio.
    """
    _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
    action = body.action.lower()
    if action not in ("start", "stop", "delete"):
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    try:
        from nifi_client import NiFiClient

        def _run() -> dict[str, Any]:
            client = NiFiClient()
            client.login()
            if action == "start":
                client.start_process_group(body.group_id)
                return {"action": "start", "ok": True}
            if action == "stop":
                client.stop_process_group(body.group_id)
                return {"action": "stop", "ok": True}
            status = client.delete_process_group(body.group_id)
            return {"action": "delete", "ok": status == 200, "status": status}

        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_spec(spec_path: str | None, inline: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve a spec by path (relative to `flows/`) or from inline JSON."""
    if spec_path:
        safe = _safe_flow_name(spec_path)
        path = (FLOWS_DIR / safe).resolve()
        if not str(path).startswith(str(FLOWS_DIR.resolve())):
            raise HTTPException(status_code=400, detail="spec path escapes flows/")
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"spec not found: {safe}")
        return json.loads(path.read_text(encoding="utf-8"))
    if inline is not None:
        return dict(inline)
    raise HTTPException(status_code=400, detail="spec_path or spec is required")


def _test_json_to_csv_flow(
    result: dict[str, Any],
    spec: dict[str, Any],
    started: float,
    client: Any | None = None,
    golden: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Verify the JSON→CSV group, including that a CSV actually landed on disk.

    Reuses the caller's authenticated client when given, so the deploy path
    doesn't re-login just to run the report card. When `golden` is given the
    report card also compares the flow's real output to expected contents.
    """
    group_id = (result or {}).get("processGroupId")
    if not group_id:
        return None
    try:
        from flow_tests import run_flow_tests
        from json_to_csv import output_dir
        from nifi_client import NiFiClient

        if client is None:
            client = NiFiClient()
            client.login()
        return run_flow_tests(
            client,
            group_id,
            golden=golden,
            spec=spec,
            output_dir=output_dir(),
            started_at=started,
            wait_seconds=20,
        )
    except Exception as exc:  # noqa: BLE001 - never fail a deploy over its report card
        return {
            "processGroupId": group_id,
            "cases": [
                {
                    "id": "suite",
                    "name": "Flow test suite",
                    "status": "fail",
                    "description": "Post-deployment checks ran against the new process group.",
                    "details": f"The suite could not run: {exc}",
                }
            ],
            "summary": {"total": 1, "passed": 0, "failed": 1, "warned": 0, "skipped": 0, "ok": False},
        }


@app.post("/api/create-flow")
async def create_flow(body: CreateFlowRequest) -> StreamingResponse:
    nifi_url = _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)

    async def event_stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(obj: dict[str, Any]) -> None:
            payload = json.dumps(obj, ensure_ascii=False)
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        async def runner() -> None:
            try:
                emit({"type": "status", "message": "Starting Cursor local agent..."})
                await asyncio.to_thread(
                    _run_agent_sync,
                    body.api_key.strip(),
                    body.model.strip(),
                    body.prompt.strip(),
                    nifi_url,
                    body.nifi_username.strip(),
                    body.nifi_password,
                    body.source_json,
                    body.source_filename,
                    emit,
                    body.selected_columns,
                )
                emit({"type": "done", "message": "Finished"})
            except Exception as exc:  # noqa: BLE001
                emit(
                    {
                        "type": "error",
                        "message": str(exc),
                        "trace": traceback.format_exc(),
                    }
                )
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/prompt-assistant")
async def prompt_assistant(body: PromptAssistRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(obj: dict[str, Any]) -> None:
            payload = json.dumps(obj, ensure_ascii=False)
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        async def runner() -> None:
            try:
                await asyncio.to_thread(_run_prompt_assistant_sync, body, emit)
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _run_prompt_assistant_sync(body: PromptAssistRequest, emit: Any) -> None:
    from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions

    from prompt_assistant import build_assistant_prompt, extract_flow_prompt

    catalog_summary = None
    if body.nifi_url and body.nifi_username and body.nifi_password:
        try:
            _apply_nifi_env(body.nifi_url, body.nifi_username, body.nifi_password)
            from nifi_catalog import load_catalog

            catalog_summary = load_catalog().summary()
            emit(
                {
                    "type": "status",
                    "message": f"Using the NiFi {catalog_summary.get('version')} processor catalog.",
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit(
                {
                    "type": "status",
                    "message": (
                        "NiFi catalog unavailable, answering from general knowledge "
                        f"— processor names are not verified against your instance ({exc})."
                    ),
                }
            )

    upload_note = None
    if body.source_json and body.source_json.strip():
        try:
            from json_to_csv import analyze_upload, input_dir, output_dir

            analysis = analyze_upload(
                body.source_json,
                body.source_filename or "upload.json",
                selected_columns=body.selected_columns,
            )
            cols = ", ".join(col["name"] for col in analysis["columns"][:30])
            upload_note = (
                f"file={analysis['filename']}; shape={analysis['shape']}; "
                f"records={analysis['recordCount']}; columns to keep={cols}.\n"
                f"GetFile input directory = {input_dir()}\n"
                f"PutFile output directory = {output_dir()}\n"
                "If the scenario is a conversion, the prompt must read with GetFile and write "
                "with PutFile using those directories — never GenerateFlowFile."
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "status", "message": f"Uploaded JSON could not be analyzed: {exc}"})

    prompt = build_assistant_prompt(
        body.scenario.strip(),
        catalog_summary=catalog_summary,
        history=[turn.model_dump() for turn in body.history],
        upload_note=upload_note,
    )

    collected: list[str] = []
    streamed_so_far = ""
    final = ""
    try:
        with Agent.create(
            model=body.model.strip(),
            api_key=body.api_key.strip(),
            local=LocalAgentOptions(cwd=str(REPO_ROOT)),
        ) as agent:
            run = agent.send(prompt)
            for message in run.stream():
                if getattr(message, "type", None) != "assistant":
                    continue
                content = getattr(getattr(message, "message", None), "content", None) or []
                for block in content:
                    if getattr(block, "type", None) != "text":
                        continue
                    text_piece = getattr(block, "text", "") or ""
                    if not text_piece:
                        continue
                    collected.append(text_piece)
                    # The Cursor SDK yields each assistant text block once as a
                    # cumulative snapshot, not as a delta. To emit true deltas
                    # to the browser we track how much has already been sent
                    # for this block and only forward what's new.
                    if text_piece.startswith(streamed_so_far):
                        delta = text_piece[len(streamed_so_far):]
                    else:
                        # A brand-new block began — send its full text as a
                        # single delta and reset the cursor.
                        delta = text_piece
                    streamed_so_far = text_piece
                    if delta:
                        emit({"type": "token", "text": delta})
            result = run.wait()
            status = getattr(result, "status", None) or "unknown"
            final = str(getattr(result, "result", None) or "")
            _record_usage(agent, run, result, body.model.strip(), source="assistant", emit=emit)
            if status == "error":
                raise RuntimeError(final or "The assistant run failed.")
    except CursorAgentError as err:
        raise RuntimeError(f"Cursor agent startup failed: {err.message}") from err

    text = final.strip() or "".join(collected).strip()
    reply, flow_prompt = extract_flow_prompt(text)
    emit({"type": "reply", "text": reply, "prompt": flow_prompt})


# --- NiFi migration ----------------------------------------------------------
#
# Separate from FLOWS_DIR because migration produces two artifacts per run (the
# migrated flow and a Markdown report) and FLOWS_DIR is a flat namespace of
# deployable .json specs that the flow list and deploy endpoints scan.

MIGRATIONS_DIR = REPO_ROOT / "migrations"
_MIGRATION_EXTENSIONS = {".json", ".md", ".xml"}


@app.get("/api/migration/versions")
async def migration_versions() -> dict[str, Any]:
    """Supported source/target versions for the dropdowns, plus rule coverage."""
    from migration_rules import rule_summary
    from nifi_versions import version_choices

    return {"versions": version_choices(), "rules": rule_summary()}


@app.post("/api/migration/inspect")
async def migration_inspect(body: MigrationInspectRequest) -> dict[str, Any]:
    """Parse the upload and report format, contents, and detected source version."""
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
    """Stream the migration analysis.

    Streamed because the optional AI pass can take a while and the deterministic
    result is worth showing the moment it is ready.
    """

    async def event_stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(obj: dict[str, Any]) -> None:
            payload = json.dumps(obj, ensure_ascii=False)
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        async def runner() -> None:
            try:
                await asyncio.to_thread(_run_migration_analysis_sync, body, emit, False)
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/migration/generate")
async def migration_generate(body: MigrationGenerateRequest) -> StreamingResponse:
    """Analyse, then generate the migrated flow and the migration report."""

    async def event_stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(obj: dict[str, Any]) -> None:
            payload = json.dumps(obj, ensure_ascii=False)
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        async def runner() -> None:
            try:
                await asyncio.to_thread(_run_migration_analysis_sync, body, emit, True)
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _run_migration_analysis_sync(
    body: MigrationAnalyzeRequest,
    emit: Any,
    generate: bool,
) -> None:
    """Deterministic analysis → optional AI enrichment → optional generation.

    Ordering matters: the deterministic verdicts are emitted before the AI runs,
    so a slow or failing model degrades the result rather than blocking it.
    """
    from flow_document import FlowParseError, detected_line, parse_flow_file
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

    emit(
        {
            "type": "status",
            "message": (
                f"Analysing {doc.summary()['totalComponents']} component(s) for "
                f"NiFi {source_version} → {target.version}."
            ),
        }
    )

    # A live catalog is the strongest evidence available, but only when the
    # connected instance actually runs the target version.
    catalog = None
    catalog_summary = None
    if body.nifi_url and body.nifi_url.strip():
        try:
            _apply_nifi_env(
                body.nifi_url, body.nifi_username or "", body.nifi_password or ""
            )
            from nifi_catalog import load_catalog

            catalog = load_catalog()
            catalog_summary = catalog.summary()
            emit(
                {
                    "type": "status",
                    "message": f"Connected to NiFi {catalog.version} for live component verification.",
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit(
                {
                    "type": "status",
                    "message": (
                        "Could not read the live NiFi catalog; falling back to the built-in "
                        f"rule base ({exc})."
                    ),
                }
            )

    analysis = analyze_migration(doc, source_version, target.version, target_catalog=catalog)
    emit({"type": "analysis", "analysis": analysis.to_dict()})

    ai_model_used = None
    if body.use_ai and body.api_key and body.model:
        ai_model_used = _enrich_with_ai(analysis, body, catalog_summary, emit)
        emit({"type": "analysis", "analysis": analysis.to_dict(), "phase": "ai"})

    if not generate:
        return

    emit({"type": "status", "message": "Generating the migrated flow…"})
    generation = generate_migrated_flow(doc, analysis)
    artifacts = _write_migration_artifacts(
        analysis, generation, body.output_name, ai_model_used
    )
    emit(
        {
            "type": "generated",
            "generation": generation.to_dict(),
            "artifacts": artifacts,
            "analysis": analysis.to_dict(),
        }
    )


def _enrich_with_ai(
    analysis: Any,
    body: MigrationAnalyzeRequest,
    catalog_summary: dict[str, Any] | None,
    emit: Any,
) -> str | None:
    """Ask the selected model about the findings the rule base could not settle.

    Any failure here is reported and swallowed: the deterministic analysis has
    already been emitted and remains valid without AI input.
    """
    from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions

    from migration_ai import (
        apply_ai_suggestions,
        build_migration_prompt,
        needs_ai_review,
        parse_ai_response,
    )

    pending = needs_ai_review(analysis)
    if not pending:
        emit(
            {
                "type": "status",
                "message": "Every component was resolved deterministically — no AI review needed.",
            }
        )
        return None

    model = (body.model or "").strip()
    emit(
        {
            "type": "status",
            "message": (
                f"Asking {model} about {len(pending)} unresolved component type(s). "
                "Deterministic verdicts are not sent for review."
            ),
        }
    )
    prompt = build_migration_prompt(analysis, pending, catalog_summary)

    collected: list[str] = []
    final = ""
    try:
        with Agent.create(
            model=model,
            api_key=(body.api_key or "").strip(),
            local=LocalAgentOptions(cwd=str(REPO_ROOT)),
        ) as agent:
            run = agent.send(prompt)
            for message in run.stream():
                if getattr(message, "type", None) != "assistant":
                    continue
                content = getattr(getattr(message, "message", None), "content", None) or []
                for block in content:
                    if getattr(block, "type", None) == "text":
                        collected.append(getattr(block, "text", "") or "")
            result = run.wait()
            final = str(getattr(result, "result", None) or "")
            _record_usage(agent, run, result, model, source="migration", emit=emit)
            if (getattr(result, "status", None) or "") == "error":
                raise RuntimeError(final or "The migration analysis run failed.")
    except CursorAgentError as err:
        analysis.warnings.append(f"AI review unavailable: {err.message}")
        emit({"type": "status", "message": f"AI review skipped: {err.message}"})
        return None
    except Exception as exc:  # noqa: BLE001
        analysis.warnings.append(f"AI review failed: {exc}")
        emit({"type": "status", "message": f"AI review failed, keeping the rule-based result: {exc}"})
        return None

    text = final.strip() or "".join(collected).strip()
    suggestions = parse_ai_response(text)
    if not suggestions:
        analysis.warnings.append(
            "The AI review returned no usable JSON; the report contains the rule-based "
            "analysis only."
        )
        emit({"type": "status", "message": "AI review returned nothing usable; ignoring it."})
        return model

    annotated = apply_ai_suggestions(analysis, suggestions)
    emit(
        {
            "type": "status",
            "message": f"AI added notes to {annotated} finding(s). They are marked as suggestions.",
        }
    )
    return model


def _write_migration_artifacts(
    analysis: Any,
    generation: Any,
    output_name: str | None,
    ai_model: str | None,
) -> dict[str, Any]:
    """Persist the migrated flow and both report formats; return download links."""
    from migration_ai import build_report, render_report_markdown
    from migration_engine import migrated_json_text

    MIGRATIONS_DIR.mkdir(exist_ok=True)
    stem = _safe_migration_stem(
        output_name
        or f"{Path(analysis.filename).stem}-nifi-{analysis.target_version}"
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{stem}"

    flow_path = MIGRATIONS_DIR / f"{base}.migrated.json"
    flow_path.write_text(migrated_json_text(generation), encoding="utf-8")

    report = build_report(analysis, generation, ai_used=ai_model)
    report_json_path = MIGRATIONS_DIR / f"{base}.report.json"
    report_json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report_md_path = MIGRATIONS_DIR / f"{base}.report.md"
    report_md_path.write_text(render_report_markdown(report), encoding="utf-8")

    def link(path: Path) -> dict[str, Any]:
        return {
            "name": path.name,
            "size": path.stat().st_size,
            "downloadUrl": f"/api/migration/download?name={path.name}",
        }

    return {
        "migratedFlow": link(flow_path),
        "reportJson": link(report_json_path),
        "reportMarkdown": link(report_md_path),
        "report": report,
    }


@app.get("/api/migration/download")
async def download_migration(name: str) -> FileResponse:
    safe = _safe_migration_name(name)
    path = (MIGRATIONS_DIR / safe).resolve()
    if not str(path).startswith(str(MIGRATIONS_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="Migration artifact not found")
    media = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".xml": "application/xml",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


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
    """Reduce a user-supplied name to a filename-safe stem."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-._")
    return (stem or "migrated-flow")[:80]


def _snapshot_flow_mtimes() -> dict[str, float]:
    FLOWS_DIR.mkdir(exist_ok=True)
    out: dict[str, float] = {}
    for path in FLOWS_DIR.rglob("*.json"):
        rel = path.relative_to(FLOWS_DIR).as_posix()
        out[rel] = path.stat().st_mtime
    return out


def _newest_created_or_updated_flow(before: dict[str, float]) -> Path | None:
    after = _snapshot_flow_mtimes()
    candidates: list[tuple[float, str]] = []
    for name, mtime in after.items():
        if name.startswith("templates/"):
            continue
        if name not in before or mtime > before[name] + 0.01:
            candidates.append((mtime, name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return FLOWS_DIR / candidates[0][1]


def _extract_flow_path_from_text(text: str) -> Path | None:
    matches = re.findall(r"flows[/\\][\w.\-/\\]+\.json", text or "")
    for match in reversed(matches):
        rel = match.replace("\\", "/").split("flows/", 1)[-1]
        path = FLOWS_DIR / rel
        if path.is_file():
            return path
    return None


def _run_agent_sync(
    api_key: str,
    model: str,
    user_prompt: str,
    nifi_url: str,
    nifi_username: str,
    nifi_password: str,
    source_json: str | None,
    source_filename: str | None,
    emit: Any,
    selected_columns: list[str] | None = None,
) -> None:
    from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions

    from agent_prompt import build_agent_prompt
    from nifi_catalog import load_catalog

    os.environ["NIFI_URL"] = nifi_url
    os.environ["NIFI_USERNAME"] = nifi_username
    os.environ["NIFI_PASSWORD"] = nifi_password

    catalog_summary = None
    try:
        emit({"type": "status", "message": "Inspecting NiFi version and available processors (used for all flows)..."})
        catalog = load_catalog()
        catalog_summary = catalog.summary()
        emit({"type": "nifi_info", "nifi": catalog_summary})
        emit(
            {
                "type": "status",
                "message": (
                    f"NiFi {catalog.version} catalog loaded. "
                    f"Strategies: {catalog.strategies()}"
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Cannot create a flow without detecting this NiFi version: {exc}"
        ) from exc

    source_note = None
    if source_json and source_json.strip():
        try:
            from json_to_csv import analyze_upload

            analysis = analyze_upload(
                source_json,
                source_filename or "upload.json",
                selected_columns=selected_columns,
            )
            for note in analysis.get("parseNotes") or []:
                emit({"type": "status", "message": note})
            cols = ", ".join(col["name"] for col in analysis["columns"][:30])
            dropped = analysis.get("droppedColumns") or []
            excluded_note = (
                "The user DESELECTED these fields — they must NOT appear in the CSV, the Jolt "
                f"spec, or the writer schema: {', '.join(dropped)}.\n"
                if dropped
                else ""
            )
            from json_to_csv import input_dir, output_dir, stage_input_file

            staged = stage_input_file(source_json, source_filename or "upload.json")
            source_note = (
                f"filename={analysis['filename']}; shape={analysis['shape']}; "
                f"records={analysis['recordCount']}; columns={cols}.\n"
                f"{excluded_note}"
                "The uploaded file was analyzed for its schema ONLY — do not embed it in the flow "
                "and do not use GenerateFlowFile. Read files with GetFile and write CSV with "
                "PutFile using these exact directories:\n"
                f"  GetFile 'Input Directory' = {input_dir()}\n"
                f"  PutFile 'Directory'       = {output_dir()}\n"
                f"A copy of the upload is already waiting in the input directory: {staged.name}\n"
                "Preferred pipeline: GetFile -> JoltTransformRecord (Record Reader=JsonTreeReader, "
                "Record Writer=CSVRecordSetWriter, DSL=Shift, spec below) -> UpdateAttribute "
                "(dynamicProperties filename -> .csv) -> PutFile.\n"
                "Per-record Jolt Specification (Shift DSL) auto-built from the upload:\n"
                f"{analysis['joltRecordSpec']}\n"
                "If this NiFi has no JoltTransformRecord, use GetFile -> JoltTransformJSON "
                "(array-level spec below) -> ConvertRecord -> UpdateAttribute -> PutFile:\n"
                f"{analysis['joltSpec']}\n"
                "CSV writer schema (use as CSVRecordSetWriter 'Schema Text' so the header and "
                f"column order are fixed):\n{analysis['avroSchema']}"
            )
            emit(
                {
                    "type": "json_analysis",
                    "analysis": {
                        "filename": analysis["filename"],
                        "shape": analysis["shape"],
                        "recordCount": analysis["recordCount"],
                        "columns": analysis["columns"],
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "status", "message": f"Uploaded JSON could not be analyzed: {exc}"})

    from agent_prompt import DEPLOYMENT_MANDATE, needs_deployment_mandate

    if needs_deployment_mandate(user_prompt):
        emit(
            {
                "type": "status",
                "message": f"Added standing requirement to your prompt: {DEPLOYMENT_MANDATE}",
            }
        )

    full_prompt = build_agent_prompt(
        user_prompt,
        nifi_url=nifi_url,
        catalog_summary=catalog_summary,
        source_json_note=source_note,
    )
    FLOWS_DIR.mkdir(exist_ok=True)
    before = _snapshot_flow_mtimes()
    started = time.time()

    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=str(REPO_ROOT)),
        ) as agent:
            emit({"type": "status", "message": f"Agent {agent.agent_id} created (model={model})"})
            emit({"type": "status", "message": f"Using NiFi at {nifi_url} (credentials from UI session)"})
            run = agent.send(full_prompt)
            emit({"type": "status", "message": f"Run started: {run.id}"})

            for message in run.stream():
                _emit_sdk_message(message, emit)

            result = run.wait()
            status = getattr(result, "status", None) or "unknown"
            text = getattr(result, "result", None) or ""
            emit({"type": "result", "status": status, "text": str(text)})
            _record_usage(agent, run, result, model, source="flow", emit=emit)

            flow_path = _extract_flow_path_from_text(str(text)) or _newest_created_or_updated_flow(before)
            if flow_path and flow_path.is_file() and flow_path.stat().st_mtime >= started - 1:
                rel = flow_path.relative_to(FLOWS_DIR).as_posix()
                content = flow_path.read_text(encoding="utf-8")
                emit(
                    {
                        "type": "flow_json",
                        "name": rel,
                        "downloadUrl": f"/api/flows/download?name={rel}",
                        "content": content,
                    }
                )

            if status != "error":
                _emit_flow_tests(str(text), flow_path, started, emit)
                emit({"type": "status", "message": "Agent finished successfully. Refresh NiFi UI."})
                return

            details = [f"Agent run failed: {run.id}"]
            if text:
                details.append(str(text))
            try:
                if run.supports("conversation"):
                    details.append("conversation=" + str(run.conversation())[:2000])
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("\n".join(details))
    except CursorAgentError as err:
        raise RuntimeError(
            f"Cursor agent startup failed: {err.message} (retryable={err.is_retryable})"
        ) from err


def _emit_flow_tests(text: str, flow_path: Path | None, started: float, emit: Any) -> None:
    """Verify whatever group the agent just deployed, found from its answer or its spec."""
    try:
        from flow_tests import extract_group_id, find_group_id_by_name, run_flow_tests
        from nifi_client import NiFiClient

        spec = None
        if flow_path and flow_path.is_file():
            try:
                spec = json.loads(flow_path.read_text(encoding="utf-8"))
            except ValueError:
                spec = None

        group_id = extract_group_id(text)
        client = NiFiClient()
        client.login()
        if not group_id and spec:
            group_id = find_group_id_by_name(client, str(spec.get("processGroupName") or ""))
        if not group_id:
            emit(
                {
                    "type": "status",
                    "message": (
                        "Skipped the flow test suite: could not tell which process group "
                        "was deployed from the agent's answer."
                    ),
                }
            )
            return

        emit({"type": "status", "message": f"Running flow tests against group {group_id}..."})
        report = run_flow_tests(client, group_id, spec=spec, started_at=started, wait_seconds=12)
        emit({"type": "tests", "report": report})
    except Exception as exc:  # noqa: BLE001
        emit({"type": "status", "message": f"Flow test suite could not run: {exc}"})


def _record_usage(
    agent: Any,
    run: Any,
    result: Any,
    model: str,
    source: str,
    emit: Any,
) -> None:
    """Add this run's token usage to the per-model totals. Never fails a run."""
    from usage_tracker import record

    try:
        usage = getattr(run, "usage", None) or getattr(result, "usage", None)
        cost = None
        try:
            agent_usage = agent.get_usage()
            cost = getattr(agent_usage, "cost", None)
            if usage is None:
                usage = getattr(agent_usage, "usage", None)
        except Exception:  # noqa: BLE001 - cost/usage lookup is best effort
            pass

        counts = record(model, usage, source=source, cost=cost)
        if not counts:
            emit(
                {
                    "type": "status",
                    "message": "This run reported no token usage, so the usage table is unchanged.",
                }
            )
            return
        emit({"type": "usage", "model": model, "counts": counts, "source": source})
    except Exception as exc:  # noqa: BLE001
        emit({"type": "status", "message": f"Could not record token usage: {exc}"})


def _emit_sdk_message(message: Any, emit: Any) -> None:
    msg_type = getattr(message, "type", None) or getattr(message, "kind", None)

    if msg_type == "assistant":
        content = getattr(getattr(message, "message", None), "content", None) or []
        parts: list[str] = []
        for block in content:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        text = "".join(parts).strip()
        if text:
            emit({"type": "assistant", "text": text})
        return

    if msg_type == "thinking":
        text = (getattr(message, "text", None) or "").strip()
        if text:
            emit({"type": "thinking", "text": text[:500]})
        return

    if msg_type == "status":
        status = getattr(message, "status", "") or ""
        text = getattr(message, "message", "") or ""
        combined = f"agent status={status} {text}".strip()
        emit({"type": "status", "message": combined})
        lowered = combined.lower()
        if "model blocked" in lowered or "blocked by your team" in lowered:
            emit(
                {
                    "type": "status",
                    "message": (
                        "This model is blocked by team admin settings. "
                        "Pick an allowed model (e.g. composer-2.5 or auto) and try again."
                    ),
                }
            )
        return

    if msg_type == "task":
        text = getattr(message, "text", "") or ""
        status = getattr(message, "status", "") or ""
        emit({"type": "status", "message": f"task {status}: {text}".strip()})
        return

    if msg_type == "tool_call":
        name = getattr(message, "name", None) or "tool"
        status = getattr(message, "status", "") or ""
        emit({"type": "tool", "name": f"{name} ({status})"})
        return

    if msg_type:
        emit({"type": "event", "name": str(msg_type)})


def main() -> None:
    import uvicorn

    os.chdir(REPO_ROOT)
    # Defaults keep local dev on loopback-only; Docker sets FLOW_STUDIO_HOST=0.0.0.0
    # so the server is reachable from outside the container.
    host = os.getenv("FLOW_STUDIO_HOST", "127.0.0.1")
    port = int(os.getenv("FLOW_STUDIO_PORT", "7860"))
    # Set FLOW_STUDIO_RELOAD=1 while editing the flow-building modules; otherwise a
    # long-running server keeps serving the code it imported at startup.
    reload = os.getenv("FLOW_STUDIO_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("ui.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
