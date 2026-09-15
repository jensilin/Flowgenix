"""Infer a CSV-oriented schema from uploaded JSON."""

from __future__ import annotations

import json
from typing import Any

_MAX_RECORDS = 50
_MAX_PAYLOAD_CHARS = 80_000

# Whitespace plus separators seen between concatenated JSON values:
# commas (comma-separated objects without array brackets), semicolons, and the
# RFC 7464 record separator used by JSON text sequences.
_VALUE_SEPARATORS = " \t\r\n,;\x1e"


def parse_json_text(text: str, notes: list[str] | None = None) -> Any:
    """Parse uploaded text as JSON, NDJSON/JSON Lines, or concatenated JSON values.

    Accepts any of:
      - a single JSON object or array (standard `.json`)
      - NDJSON / JSON Lines: one JSON value per line, no wrapping array
      - JSON values concatenated or comma-separated without array brackets
      - a bare scalar, which is treated as a single record
      - messy captures (REST responses with log prefixes, or non-JSON lines mixed
        in): every JSON value that can be recovered is kept and the rest reported

    Anything appended to ``notes`` is a human-readable warning about content that
    had to be recovered or skipped, so callers can surface it to the user.
    """
    cleaned = (text or "").lstrip("\ufeff").strip()
    if not cleaned:
        raise ValueError("Uploaded file is empty — expected JSON, NDJSON, or JSON Lines.")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = _parse_json_stream(cleaned, notes)

    if isinstance(data, (dict, list)):
        return data
    return [data]


def _parse_json_stream(text: str, notes: list[str] | None = None) -> list[Any]:
    """Read a sequence of JSON values (NDJSON / concatenated) into a list."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    idx = 0
    length = len(text)

    while idx < length:
        while idx < length and text[idx] in _VALUE_SEPARATORS:
            idx += 1
        if idx >= length:
            break
        try:
            value, idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            # Not a clean value sequence; fall back to recovering what we can.
            return _salvage_json_values(text, notes)
        values.append(value)

    if not values:
        return _salvage_json_values(text, notes)
    return values


def _salvage_json_values(text: str, notes: list[str] | None = None) -> list[Any]:
    """Recover every JSON value we can, line by line, ignoring non-JSON noise.

    Handles captures where JSON is embedded after a prefix (log timestamps, HTTP
    status lines) or interleaved with lines that are not JSON at all.
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    skipped: list[int] = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().strip("".join(c for c in _VALUE_SEPARATORS if not c.isspace()))
        if not line:
            continue
        try:
            values.append(json.loads(line))
            continue
        except json.JSONDecodeError:
            pass

        # Recover JSON values embedded in the line, e.g. "12:03 INFO {...}" or the
        # members of a malformed array like '[{"a":1},{"a":2},]'.
        recovered = 0
        pos = 0
        while pos < len(line):
            if line[pos] not in "{[":
                pos += 1
                continue
            try:
                value, pos = decoder.raw_decode(line, pos)
            except json.JSONDecodeError:
                pos += 1
                continue
            values.append(value)
            recovered += 1
        if not recovered:
            skipped.append(lineno)

    if not values:
        raise ValueError(_no_json_message(text))

    if skipped and notes is not None:
        shown = ", ".join(str(n) for n in skipped[:10])
        more = f" (+{len(skipped) - 10} more)" if len(skipped) > 10 else ""
        notes.append(
            f"Skipped {len(skipped)} line(s) that were not valid JSON: {shown}{more}. "
            f"Analyzed the {len(values)} JSON value(s) that were recovered."
        )
    return values


def _no_json_message(text: str) -> str:
    head = text.lstrip()[:120].replace("\n", " ")
    if head.startswith("<"):
        kind = "looks like HTML or XML, not JSON"
    elif "{" not in text and "[" not in text:
        kind = "has no JSON objects at all (e.g. CSV, or Prometheus text exposition format)"
    else:
        kind = "is not valid JSON"
    return (
        f"No JSON could be parsed — the content {kind}. First characters: {head!r}. "
        "Supported formats: a JSON object, a JSON array, or NDJSON/JSON Lines "
        "(one JSON value per line)."
    )


def analyze_json(
    data: Any,
    filename: str = "upload.json",
    selected_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Infer CSV columns from JSON.

    `selected_columns` restricts the output to the chosen fields (matched by either
    the dotted source path or the flat CSV name). Everything derived from the
    columns — the Avro schema, both Jolt specs, the sample payload — is built from
    the surviving columns only, so unselected fields never reach the CSV.
    """
    nested_field = None
    if isinstance(data, list):
        shape = "array"
        raw_records = data
    else:
        nested = _largest_object_array(data)
        if nested:
            shape = "nested_array"
            nested_field, raw_records = nested
        else:
            shape = "object"
            raw_records = [data]

    normalized_raw = [_as_record(item, i) for i, item in enumerate(raw_records)]
    records = [flatten_record(rec) for rec in normalized_raw]
    columns: dict[str, dict[str, Any]] = {}
    for record in records[:_MAX_RECORDS]:
        for key, value in record.items():
            slot = columns.setdefault(
                key,
                {"name": key, "avroName": _avro_name(key), "kind": "null", "sample": None},
            )
            slot["kind"] = _widen(slot["kind"], _value_kind(value))
            if slot["sample"] is None and value is not None:
                slot["sample"] = _sample(value)

    column_list = list(columns.values())
    if not column_list:
        raise ValueError("Could not infer any CSV columns from the uploaded JSON.")

    available = [col["name"] for col in column_list]
    column_list, dropped, unknown = _apply_selection(column_list, selected_columns)

    # Reference samples for inspection/debugging only — the generated flow reads real
    # files via GetFile, so neither payload is embedded in a processor.
    sample_records = [_rename_keys(rec, column_list) for rec in records[:20]]
    payload = json.dumps(sample_records, ensure_ascii=False, indent=2)
    truncated = len(records) > 20
    if len(payload) > _MAX_PAYLOAD_CHARS:
        payload = json.dumps(sample_records[:5], ensure_ascii=False, separators=(",", ":"))
        truncated = True

    # Raw (still-nested) sample, kept for reference alongside the flattened one.
    raw_sample = normalized_raw[:20]
    raw_payload = json.dumps(raw_sample, ensure_ascii=False, indent=2)
    raw_truncated = len(normalized_raw) > 20
    if len(raw_payload) > _MAX_PAYLOAD_CHARS:
        raw_payload = json.dumps(normalized_raw[:5], ensure_ascii=False, separators=(",", ":"))
        raw_truncated = True

    jolt_spec = build_jolt_shift_spec(column_list)
    jolt_record_spec = build_jolt_record_shift_spec(column_list)

    return {
        "filename": filename,
        "shape": shape,
        "nestedField": nested_field,
        "recordCount": len(records),
        "columns": column_list,
        "avroSchema": _avro_schema(column_list),
        "samplePayload": payload,
        "truncated": truncated,
        "rawSamplePayload": raw_payload,
        "rawTruncated": raw_truncated,
        "joltSpec": json.dumps(jolt_spec, ensure_ascii=False, indent=2),
        "joltRecordSpec": json.dumps(jolt_record_spec, ensure_ascii=False, indent=2),
        "availableColumns": available,
        "droppedColumns": dropped,
        "unknownSelections": unknown,
    }


def _apply_selection(
    column_list: list[dict[str, Any]], selected: list[str] | None
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Keep only the chosen columns, in their original (inferred) order."""
    if selected is None:
        return column_list, [], []

    wanted = {str(name).strip() for name in selected if str(name).strip()}
    kept = [c for c in column_list if c["name"] in wanted or c["avroName"] in wanted]
    if not kept:
        raise ValueError(
            "No matching columns were selected — pick at least one field to include in the CSV."
        )
    known = {c["name"] for c in column_list} | {c["avroName"] for c in column_list}
    dropped = [c["name"] for c in column_list if c not in kept]
    return kept, dropped, sorted(wanted - known)


def build_jolt_shift_spec(columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Auto-build a JoltTransformJSON 'shift' spec that flattens nested paths.

    Each inferred column has a dotted `name` (its path in the original nested
    JSON) and a flat `avroName` (its safe CSV column name). JoltTransformJSON
    is applied to an array of the raw records, so every leaf output path is
    prefixed with `[&N]` where N is the number of segments in the dotted path
    — that's how many spec levels separate the leaf from the top-level `*`
    wildcard that matches the array index.
    """
    return {"*": _shift_tree(columns, per_record=False)}


def build_jolt_record_shift_spec(columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Auto-build a JoltTransformRecord 'shift' spec that flattens nested paths.

    JoltTransformRecord applies the spec to **one record at a time** (the reader has
    already split the input into records), so unlike `build_jolt_shift_spec` there is
    no top-level `*` wildcard for an array index and no `[&N]` output prefixes — each
    leaf simply maps to its flat column name.
    """
    return _shift_tree(columns, per_record=True)


def _shift_tree(columns: list[dict[str, Any]], per_record: bool) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for col in columns:
        path = str(col["name"])
        avro = str(col["avroName"])
        segments = [seg for seg in path.split(".") if seg] or [path]
        node = tree
        for seg in segments[:-1]:
            existing = node.get(seg)
            if not isinstance(existing, dict):
                existing = {}
                node[seg] = existing
            node = existing
        node[segments[-1]] = avro if per_record else f"[&{len(segments)}].{avro}"
    return tree


def flatten_record(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(flatten_record(value, path))
            elif isinstance(value, list):
                out[path] = json.dumps(value, ensure_ascii=False)
            else:
                out[path] = value
        return out
    return {prefix or "value": obj}


def _rename_keys(record: dict[str, Any], columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Project a flattened record onto the declared columns, dropping the rest."""
    mapping = {col["name"]: col["avroName"] for col in columns}
    return {
        mapping[key]: value for key, value in record.items() if key in mapping
    }


def _as_record(item: Any, index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {"value": item, "index": index}


def _largest_object_array(obj: dict[str, Any]) -> tuple[str, list[Any]] | None:
    best: tuple[str, list[Any]] | None = None
    for key, value in obj.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value[:10]):
            if best is None or len(value) > len(best[1]):
                best = (key, value)
    return best


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "long"
    if isinstance(value, float):
        return "double"
    return "string"


def _widen(current: str, incoming: str) -> str:
    if current == "null":
        return incoming
    if incoming == "null" or incoming == current:
        return current
    if {current, incoming} <= {"long", "double"}:
        return "double"
    return "string"


def _sample(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 80:
        return value[:77] + "..."
    return value


def _avro_name(name: str) -> str:
    chars = []
    for ch in name.replace(".", "_").replace(" ", "_"):
        if ch.isalnum() or ch == "_":
            chars.append(ch)
        else:
            chars.append("_")
    out = "".join(chars) or "field"
    if out[0].isdigit():
        out = f"f_{out}"
    return out


def _avro_schema(columns: list[dict[str, Any]]) -> str:
    fields = []
    used: set[str] = set()
    for col in columns:
        avro = str(col["avroName"])
        base = avro
        n = 2
        while avro in used:
            avro = f"{base}_{n}"
            n += 1
        used.add(avro)
        col["avroName"] = avro
        kind = col["kind"] if col["kind"] != "null" else "string"
        fields.append({"name": avro, "type": ["null", kind], "default": None})
    schema = {"type": "record", "name": "nifiRecord", "fields": fields}
    return json.dumps(schema, indent=2)
