"""Build a version-aware JSON → CSV NiFi flow spec from uploaded JSON."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from json_analyze import analyze_json, parse_json_text
from nifi_catalog import NiFiCatalog, load_catalog, version_at_least

REPO_ROOT = Path(__file__).resolve().parent
FLOWS_DIR = REPO_ROOT / "flows"
DATA_DIR = REPO_ROOT / "data"

# GetFile/PutFile paths are resolved by the NiFi process, not by this script, so they
# must be absolute and visible to NiFi. Override when NiFi runs elsewhere (e.g. in a
# container) with JSON_TO_CSV_INPUT_DIR / JSON_TO_CSV_OUTPUT_DIR.
INPUT_DIR_ENV = "JSON_TO_CSV_INPUT_DIR"
OUTPUT_DIR_ENV = "JSON_TO_CSV_OUTPUT_DIR"

# When NiFi runs under WSL or in a container it sees a different filesystem, so the
# path this script writes to is not the path NiFi must be told. These override the
# NiFi-facing paths only; staging still uses the local ones above.
NIFI_INPUT_DIR_ENV = "JSON_TO_CSV_NIFI_INPUT_DIR"
NIFI_OUTPUT_DIR_ENV = "JSON_TO_CSV_NIFI_OUTPUT_DIR"

_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")

# Matches the JSON/NDJSON extensions the analyzer accepts.
JSON_FILE_FILTER = r"(?i).*\.(json|ndjson|jsonl|jsonlines|txt|log)$"


def input_dir() -> Path:
    return Path(os.getenv(INPUT_DIR_ENV) or (DATA_DIR / "json-in")).resolve()


def output_dir() -> Path:
    return Path(os.getenv(OUTPUT_DIR_ENV) or (DATA_DIR / "csv-out")).resolve()


def ensure_data_dirs() -> tuple[Path, Path]:
    src, dest = input_dir(), output_dir()
    src.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    return src, dest


def wsl_path(path: Path | str) -> str | None:
    """`C:\\Users\\me\\data` -> `/mnt/c/Users/me/data`, or None if not a Windows path."""
    match = _WINDOWS_PATH.match(str(path))
    if not match:
        return None
    drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def directory_visible_to_nifi(client: Any, catalog: NiFiCatalog, path: str) -> bool:
    """Ask NiFi whether it can see a directory.

    NiFi has no filesystem API, so this uses GetFile's own validation: point a
    throwaway processor at the path and read the validation errors back.
    """
    info = catalog.resolve_processor_info("GetFile")
    root = client.root_process_group_id()
    probe = client.create_processor(
        root, f"path-probe-{int(time.time() * 1000)}", info.type_name, -400, -400,
        bundle=info.bundle,
    )
    try:
        fresh = client.get(f"/nifi-api/processors/{probe['id']}")
        client.update_processor(
            probe["id"],
            revision_version=fresh["revision"]["version"],
            properties={"Input Directory": path},
        )
        after = client.get(f"/nifi-api/processors/{probe['id']}")["component"]
        return not [
            err for err in (after.get("validationErrors") or []) if "Input Directory" in err
        ]
    finally:
        current = client.get(f"/nifi-api/processors/{probe['id']}")
        client.session.delete(
            f"{client.base_url}/nifi-api/processors/{probe['id']}"
            f"?version={current['revision']['version']}",
            timeout=30,
        )


def resolve_nifi_dirs(
    client: Any | None = None, catalog: NiFiCatalog | None = None
) -> tuple[str, str, str | None]:
    """Return the input/output directories *as NiFi must be told them*, plus a note.

    Falls back to the local paths when there is nothing to probe with, so callers
    without a client behave exactly as before.
    """
    local_in, local_out = input_dir(), output_dir()
    override_in = os.getenv(NIFI_INPUT_DIR_ENV)
    override_out = os.getenv(NIFI_OUTPUT_DIR_ENV)
    if override_in or override_out:
        return (
            override_in or str(local_in),
            override_out or str(local_out),
            f"Using the NiFi-side paths from {NIFI_INPUT_DIR_ENV}/{NIFI_OUTPUT_DIR_ENV}.",
        )

    translated_in = wsl_path(local_in)
    if not client or not catalog or not translated_in:
        return str(local_in), str(local_out), None

    if directory_visible_to_nifi(client, catalog, str(local_in)):
        return str(local_in), str(local_out), None
    if directory_visible_to_nifi(client, catalog, translated_in):
        return (
            translated_in,
            wsl_path(local_out) or str(local_out),
            "NiFi does not share this machine's filesystem directly (it looks like WSL "
            f"or a container), so the flow reads {translated_in} — the same folder as "
            f"{local_in} seen from NiFi's side.",
        )
    return (
        str(local_in),
        str(local_out),
        f"NiFi cannot see {local_in}. Mount that folder into NiFi, or set "
        f"{NIFI_INPUT_DIR_ENV}/{NIFI_OUTPUT_DIR_ENV} to paths NiFi can reach.",
    )


def stage_input_file(json_text: str, filename: str = "upload.json") -> Path:
    """Drop the uploaded JSON into the GetFile pickup directory so the flow has data.

    GetFile consumes (deletes) the file once the flow reads it, which is the normal
    pickup-directory contract.
    """
    src, _ = ensure_data_dirs()
    safe = Path(filename or "upload.json").name or "upload.json"
    target = src / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}"
    target.write_text(json_text, encoding="utf-8")
    return target


def _component(catalog: NiFiCatalog, kind: str, name: str) -> dict[str, Any]:
    info = (
        catalog.resolve_processor_info(name)
        if kind == "processor"
        else catalog.resolve_service_info(name)
    )
    return {"type": info.type_name, "bundle": info.bundle}


def build_json_to_csv_spec(
    analysis: dict[str, Any],
    catalog: NiFiCatalog,
    process_group_name: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    strategy = catalog.json_to_csv_strategy()
    if strategy == "unsupported":
        raise RuntimeError(
            f"NiFi {catalog.version} has no JSON→CSV processors "
            "(need ConvertRecord + JsonTreeReader + CSVRecordSetWriter, or ConvertJSONToCSV)."
        )
    for required in ("GetFile", "PutFile"):
        if not catalog.has_processor(required):
            raise RuntimeError(
                f"NiFi {catalog.version} has no {required}; the JSON→CSV flow reads and "
                "writes files on disk and needs both GetFile and PutFile."
            )

    slug = time.strftime("%Y%m%d-%H%M%S")
    pg_name = process_group_name or f"json-to-csv-{slug}"
    ensure_data_dirs()
    nifi_in, nifi_out, path_note = resolve_nifi_dirs(client, catalog)

    if strategy == "jolt_record":
        spec = _jolt_record_spec(catalog, analysis, pg_name, nifi_in, nifi_out)
    elif strategy == "jolt_convert_record":
        spec = _jolt_document_spec(catalog, analysis, pg_name, nifi_in, nifi_out)
    elif strategy == "convert_record":
        spec = _record_spec(catalog, analysis, pg_name, nifi_in, nifi_out)
    else:
        spec = _legacy_spec(catalog, pg_name, strategy, nifi_in, nifi_out)

    spec["nifiVersion"] = catalog.version
    spec["jsonToCsvStrategy"] = strategy
    spec["sourceAnalysis"] = {
        "filename": analysis.get("filename"),
        "shape": analysis.get("shape"),
        "nestedField": analysis.get("nestedField"),
        "recordCount": analysis.get("recordCount"),
        "columns": [col["name"] for col in analysis.get("columns") or []],
        "droppedColumns": analysis.get("droppedColumns") or [],
        "inputDirectory": str(input_dir()),
        "outputDirectory": str(output_dir()),
        "nifiInputDirectory": nifi_in,
        "nifiOutputDirectory": nifi_out,
        "pathNote": path_note,
        "joltSpec": (
            analysis.get("joltRecordSpec")
            if strategy == "jolt_record"
            else analysis.get("joltSpec") if strategy == "jolt_convert_record" else None
        ),
    }
    return spec


def _get_file(catalog: NiFiCatalog, x: float, y: float, nifi_in: str) -> dict[str, Any]:
    return {
        "name": "GetJSONFiles",
        **_component(catalog, "processor", "GetFile"),
        "x": x,
        "y": y,
        "schedulingPeriod": "10 sec",
        "properties": {
            "Input Directory": nifi_in,
            "File Filter": JSON_FILE_FILTER,
            "Keep Source File": "false",
            "Recurse Subdirectories": "false",
            "Minimum File Age": "1 sec",
            "Batch Size": "10",
        },
        "autoTerminated": [],
    }


def _name_as_csv(catalog: NiFiCatalog, x: float, y: float) -> dict[str, Any]:
    """Rename the FlowFile so PutFile writes `<original name>.csv`."""
    return {
        "name": "NameOutputCSV",
        **_component(catalog, "processor", "UpdateAttribute"),
        "x": x,
        "y": y,
        "properties": {},
        "dynamicProperties": {
            "filename": "${filename:substringBeforeLast('.')}.csv",
        },
        "autoTerminated": [],
    }


def _put_file(catalog: NiFiCatalog, x: float, y: float, nifi_out: str) -> dict[str, Any]:
    return {
        "name": "PutCSVFiles",
        **_component(catalog, "processor", "PutFile"),
        "x": x,
        "y": y,
        "properties": {
            "Directory": nifi_out,
            "Conflict Resolution Strategy": "replace",
            "Create Missing Directories": "true",
        },
        "autoTerminated": ["success", "failure"],
    }


def _csv_writer_props(analysis: dict[str, Any], schema_text: bool) -> dict[str, str]:
    props = {
        "Include Header Line": "true",
        "CSV Format": "custom",
        "Value Separator": ",",
        "Quote Character": '"',
        "Escape Character": "\\",
        "Record Separator": "\n",
        "Quote Mode": "MINIMAL",
    }
    if schema_text:
        props["Schema Access Strategy"] = "schema-text-property"
        props["Schema Text"] = analysis["avroSchema"]
    else:
        props["Schema Access Strategy"] = "inherit-record-schema"
    return props


def _json_reader_props(catalog: NiFiCatalog, analysis: dict[str, Any]) -> dict[str, str]:
    props: dict[str, str] = {}
    if version_at_least(catalog.version, 1, 9):
        props["Schema Access Strategy"] = "infer-schema"
    else:
        props["Schema Access Strategy"] = "schema-text-property"
        props["Schema Text"] = analysis["avroSchema"]
    if version_at_least(catalog.version, 1, 15):
        props["Starting Field Strategy"] = "ROOT_NODE"
    return props


def write_spec(spec: dict[str, Any], path: Path | None = None) -> Path:
    FLOWS_DIR.mkdir(exist_ok=True)
    if path is None:
        name = spec.get("processGroupName") or "json-to-csv"
        path = FLOWS_DIR / f"{name}.json"
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


def _jolt_record_spec(
    catalog: NiFiCatalog,
    analysis: dict[str, Any],
    pg_name: str,
    nifi_in: str,
    nifi_out: str,
) -> dict[str, Any]:
    """GetFile -> JoltTransformRecord (flatten + write CSV) -> UpdateAttribute -> PutFile.

    JoltTransformRecord reads through JsonTreeReader, so a picked-up file may be a JSON
    object, a JSON array, or NDJSON. The "Shift" spec is generated per record from the
    uploaded file's inferred columns (`json_analyze.build_jolt_record_shift_spec`), and
    the CSV writer uses the inferred flat schema so column order and the header are
    deterministic — Jolt's output schema differs from the nested input schema, so the
    writer cannot inherit it.
    """
    return {
        "processGroupName": pg_name,
        "position": {"x": 260, "y": 180},
        "start": True,
        "controllerServices": [
            {
                "name": "JsonTreeReader",
                **_component(catalog, "service", "JsonTreeReader"),
                "properties": _json_reader_props(catalog, analysis),
            },
            {
                "name": "CSVRecordSetWriter",
                **_component(catalog, "service", "CSVRecordSetWriter"),
                "properties": _csv_writer_props(analysis, schema_text=True),
            },
        ],
        "processors": [
            _get_file(catalog, 60, 120, nifi_in),
            {
                "name": "FlattenToCSV",
                **_component(catalog, "processor", "JoltTransformRecord"),
                "x": 380,
                "y": 120,
                "properties": {
                    "Record Reader": "@JsonTreeReader",
                    "Record Writer": "@CSVRecordSetWriter",
                    "Jolt Transformation DSL": "Shift",
                    "Jolt Specification": analysis["joltRecordSpec"],
                },
                "autoTerminated": ["failure", "original"],
            },
            _name_as_csv(catalog, 700, 120),
            _put_file(catalog, 1020, 120, nifi_out),
        ],
        "connections": [
            {"from": "GetJSONFiles", "to": "FlattenToCSV", "relationships": ["success"]},
            {"from": "FlattenToCSV", "to": "NameOutputCSV", "relationships": ["success"]},
            {"from": "NameOutputCSV", "to": "PutCSVFiles", "relationships": ["success"]},
        ],
    }


def _jolt_document_spec(
    catalog: NiFiCatalog,
    analysis: dict[str, Any],
    pg_name: str,
    nifi_in: str,
    nifi_out: str,
) -> dict[str, Any]:
    """GetFile -> JoltTransformJSON -> ConvertRecord -> UpdateAttribute -> PutFile.

    Fallback for NiFi builds without JoltTransformRecord. JoltTransformJSON needs one
    whole JSON document per FlowFile, so picked-up files must be a JSON array or object
    — NDJSON input will fail here.
    """
    return {
        "processGroupName": pg_name,
        "position": {"x": 260, "y": 180},
        "start": True,
        "controllerServices": [
            {
                "name": "JsonTreeReader",
                **_component(catalog, "service", "JsonTreeReader"),
                "properties": _json_reader_props(catalog, analysis),
            },
            {
                "name": "CSVRecordSetWriter",
                **_component(catalog, "service", "CSVRecordSetWriter"),
                "properties": _csv_writer_props(
                    analysis, schema_text=not version_at_least(catalog.version, 1, 9)
                ),
            },
        ],
        "processors": [
            _get_file(catalog, 60, 120, nifi_in),
            {
                "name": "FlattenWithJolt",
                **_component(catalog, "processor", "JoltTransformJSON"),
                "x": 380,
                "y": 120,
                "properties": {
                    "Jolt Transformation DSL": "Shift",
                    "Jolt Specification": analysis["joltSpec"],
                    "Pretty Print": "false",
                },
                "autoTerminated": ["failure"],
            },
            {
                "name": "ConvertJSONToCSV",
                **_component(catalog, "processor", "ConvertRecord"),
                "x": 700,
                "y": 120,
                "properties": {
                    "Record Reader": "@JsonTreeReader",
                    "Record Writer": "@CSVRecordSetWriter",
                },
                "autoTerminated": ["failure"],
            },
            _name_as_csv(catalog, 1020, 120),
            _put_file(catalog, 1340, 120, nifi_out),
        ],
        "connections": [
            {"from": "GetJSONFiles", "to": "FlattenWithJolt", "relationships": ["success"]},
            {"from": "FlattenWithJolt", "to": "ConvertJSONToCSV", "relationships": ["success"]},
            {"from": "ConvertJSONToCSV", "to": "NameOutputCSV", "relationships": ["success"]},
            {"from": "NameOutputCSV", "to": "PutCSVFiles", "relationships": ["success"]},
        ],
    }


def _record_spec(
    catalog: NiFiCatalog,
    analysis: dict[str, Any],
    pg_name: str,
    nifi_in: str,
    nifi_out: str,
) -> dict[str, Any]:
    """GetFile -> ConvertRecord -> UpdateAttribute -> PutFile (no Jolt available).

    Without Jolt the flow cannot flatten nested objects, so the reader is pinned to the
    inferred flat schema and nested inputs may not map cleanly.
    """
    return {
        "processGroupName": pg_name,
        "position": {"x": 260, "y": 180},
        "start": True,
        "controllerServices": [
            {
                "name": "JsonTreeReader",
                **_component(catalog, "service", "JsonTreeReader"),
                "properties": _json_reader_props(catalog, analysis),
            },
            {
                "name": "CSVRecordSetWriter",
                **_component(catalog, "service", "CSVRecordSetWriter"),
                "properties": _csv_writer_props(
                    analysis, schema_text=not version_at_least(catalog.version, 1, 9)
                ),
            },
        ],
        "processors": [
            _get_file(catalog, 80, 120, nifi_in),
            {
                "name": "ConvertJSONToCSV",
                **_component(catalog, "processor", "ConvertRecord"),
                "x": 420,
                "y": 120,
                "properties": {
                    "Record Reader": "@JsonTreeReader",
                    "Record Writer": "@CSVRecordSetWriter",
                },
                "autoTerminated": ["failure"],
            },
            _name_as_csv(catalog, 760, 120),
            _put_file(catalog, 1080, 120, nifi_out),
        ],
        "connections": [
            {"from": "GetJSONFiles", "to": "ConvertJSONToCSV", "relationships": ["success"]},
            {"from": "ConvertJSONToCSV", "to": "NameOutputCSV", "relationships": ["success"]},
            {"from": "NameOutputCSV", "to": "PutCSVFiles", "relationships": ["success"]},
        ],
    }


def _legacy_spec(
    catalog: NiFiCatalog,
    pg_name: str,
    strategy: str,
    nifi_in: str,
    nifi_out: str,
) -> dict[str, Any]:
    """GetFile -> legacy converters -> UpdateAttribute -> PutFile (pre-record NiFi)."""
    if strategy == "legacy_json_avro_csv":
        return {
            "processGroupName": pg_name,
            "position": {"x": 260, "y": 180},
            "start": True,
            "controllerServices": [],
            "processors": [
                _get_file(catalog, 80, 120, nifi_in),
                {
                    "name": "ConvertJSONToAvro",
                    **_component(catalog, "processor", "ConvertJSONToAvro"),
                    "x": 380,
                    "y": 120,
                    "properties": {},
                    "autoTerminated": ["failure"],
                },
                {
                    "name": "ConvertAvroToCSV",
                    **_component(catalog, "processor", "ConvertAvroToCSV"),
                    "x": 680,
                    "y": 120,
                    "properties": {},
                    "autoTerminated": ["failure"],
                },
                _name_as_csv(catalog, 980, 120),
                _put_file(catalog, 1280, 120, nifi_out),
            ],
            "connections": [
                {"from": "GetJSONFiles", "to": "ConvertJSONToAvro", "relationships": ["success"]},
                {"from": "ConvertJSONToAvro", "to": "ConvertAvroToCSV", "relationships": ["success"]},
                {"from": "ConvertAvroToCSV", "to": "NameOutputCSV", "relationships": ["success"]},
                {"from": "NameOutputCSV", "to": "PutCSVFiles", "relationships": ["success"]},
            ],
        }
    return {
        "processGroupName": pg_name,
        "position": {"x": 260, "y": 180},
        "start": True,
        "controllerServices": [],
        "processors": [
            _get_file(catalog, 80, 120, nifi_in),
            {
                "name": "ConvertJSONToCSV",
                **_component(catalog, "processor", "ConvertJSONToCSV"),
                "x": 420,
                "y": 120,
                "properties": {},
                "autoTerminated": ["failure"],
            },
            _name_as_csv(catalog, 760, 120),
            _put_file(catalog, 1080, 120, nifi_out),
        ],
        "connections": [
            {"from": "GetJSONFiles", "to": "ConvertJSONToCSV", "relationships": ["success"]},
            {"from": "ConvertJSONToCSV", "to": "NameOutputCSV", "relationships": ["success"]},
            {"from": "NameOutputCSV", "to": "PutCSVFiles", "relationships": ["success"]},
        ],
    }


def analyze_upload(
    json_text: str,
    filename: str = "upload.json",
    selected_columns: list[str] | None = None,
) -> dict[str, Any]:
    notes: list[str] = []
    analysis = analyze_json(
        parse_json_text(json_text, notes),
        filename=filename,
        selected_columns=selected_columns,
    )
    if analysis.get("droppedColumns"):
        notes.append(
            "Excluded by your field selection: "
            + ", ".join(analysis["droppedColumns"])
        )
    if analysis.get("unknownSelections"):
        notes.append(
            "Ignored unknown field(s) in the selection: "
            + ", ".join(analysis["unknownSelections"])
        )
    if notes:
        analysis["parseNotes"] = notes
    return analysis


def build_from_upload(
    json_text: str,
    filename: str = "upload.json",
    catalog: NiFiCatalog | None = None,
    stage_input: bool = True,
    selected_columns: list[str] | None = None,
    client: Any | None = None,
) -> tuple[dict[str, Any], Path]:
    catalog = catalog or load_catalog()
    analysis = analyze_upload(json_text, filename, selected_columns=selected_columns)
    spec = build_json_to_csv_spec(analysis, catalog, client=client)
    if stage_input:
        # Give GetFile something to pick up immediately, so the flow produces CSV as
        # soon as it starts instead of waiting for a file to be dropped by hand.
        staged = stage_input_file(json_text, filename)
        spec["sourceAnalysis"]["stagedInputFile"] = str(staged)
    path = write_spec(spec)
    return spec, path
