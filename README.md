# Flowgenix — NiFi flow migration

Upload an Apache NiFi XML template or JSON flow definition, pick the source and
target versions, and download a file the destination can import.

- **XML → JSON** for any 1.x template (required for 2.x — templates were removed in 2.0)
- **JSON → XML** when the target is still 1.x
- **Version dialects** — JSON fields added after NiFi **2.6** are stripped when
  targeting 2.6, and filled with defaults when targeting a later 2.x. XML tag
  aliases from older encodings are accepted on parse and written in the target
  encoding.

The tool is **offline**. It does not talk to a live NiFi or to Cursor.

## Run

```powershell
python -m pip install -r requirements.txt
python -m ui.app
```

Open **http://127.0.0.1:7860/**.

1. Drop a `.xml` template or `.json` flow definition.
2. Confirm source version (detected from NAR bundle stamps when present).
3. Pick the target version and output format (JSON, or XML on 1.x targets).
4. **Analyze**, then **Generate migrated flow**.
5. Download the migrated file and the Markdown/JSON report.

## What it changes

Deterministic rules rewrite known property names (for example Jolt’s
`Jolt Transformation DSL` → `Jolt Transform`), restamp NAR bundle versions, and
convert `EVENT_DRIVEN` scheduling on 2.x. Removed processors (GetHTTP, PostHTTP,
…) are **left unchanged** and marked for manual review — they are never silently
replaced.

Everything else is copied verbatim, so the imported canvas matches the one you
exported: identifiers, positions, properties and their descriptors,
`annotationData`, relationship flags, connection endpoints, and the
process-group hierarchy. To confirm that on your own template:

```powershell
python tests/check_template_fidelity.py path\to\template.xml 2.6.0
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FLOWGENIX_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `FLOWGENIX_PORT` | `7860` | HTTP port |
| `FLOWGENIX_RELOAD` | off | Set `1` to reload on code changes |

`FLOW_STUDIO_HOST` / `FLOW_STUDIO_PORT` are still accepted as aliases.

## Docker

```bash
./script.sh
```

Open http://localhost:7860/. Artifacts land in `./migrations` on the host.

## Tests

```powershell
python -m pytest
```
