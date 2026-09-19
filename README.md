# Flowgenix — NiFi flow migration

Upload an Apache NiFi XML template or JSON flow definition, pick the source and
target versions, and download a file the destination can import.

- **XML → JSON** for any 1.x template (required for 2.x — templates were removed in 2.0)
- **JSON → XML** when the target is still 1.x
- **Version dialects** — JSON fields added after NiFi **2.6** are stripped when
  targeting 2.6, and filled with defaults when targeting a later 2.x. XML tag
  aliases from older encodings are accepted on parse and written in the target
  encoding.

The tool is **offline**. It does not talk to a live NiFi, and it stores nothing:
a migration is parsed, converted and handed back inside the same response, and
the browser saves the files.

## Run locally

```powershell
python -m pip install -r requirements-dev.txt
python -m ui.app
```

Open **http://127.0.0.1:7860/**.

1. Drop a `.xml` template or `.json` flow definition.
2. Confirm source version (detected from NAR bundle stamps when present).
3. Pick the target version and output format (JSON, or XML on 1.x targets).
4. **Analyze**, then **Generate migrated flow**.
5. Download the migrated file and the Markdown/JSON report.

The download is named after the process group inside the flow, because NiFi
names an imported group after the file it was uploaded from.

## Deploy to Vercel

The app ships as a single Python function. `api/index.py` hands every request to
the same FastAPI app you run locally, and `vercel.json` rewrites all traffic to
it, so the page, the static assets and the API come from one deployment.

```powershell
npm i -g vercel
vercel          # preview
vercel --prod   # production
```

A Git-connected project needs no configuration beyond this repo: Vercel reads
`requirements.txt` for the runtime dependencies and `.python-version` for the
interpreter. No environment variables are required.

Two hosting limits are worth knowing before you upload a very large flow:

| Limit | Value | What it means here |
|---|---|---|
| Request body | 4.5 MB | The template you upload must be under it |
| Response body | 4.5 MB | The migrated flow plus the report come back together |
| Function duration | 60 s (`vercel.json`) | Enough for flows of a few thousand components |

Flows past those limits still migrate fine locally or in Docker, where nothing
travels over a serverless boundary. The run log may also arrive in one burst
rather than line by line, depending on how the platform buffers the response;
the result is identical either way.

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
| `FLOWGENIX_ARCHIVE_DIR` | unset | Also keep every migration on disk |

Downloads do not depend on `FLOWGENIX_ARCHIVE_DIR`; it only adds a server-side
copy. Leave it unset on Vercel, where the filesystem is read-only.
`FLOW_STUDIO_HOST` / `FLOW_STUDIO_PORT` are still accepted as aliases.

## Docker

```bash
./script.sh
```

Open http://localhost:7860/. The image sets `FLOWGENIX_ARCHIVE_DIR`, so a copy
of every migration also lands in `./migrations` on the host.

## Tests

```powershell
python -m pytest                       # unit and wiring tests
python tests/smoke_migration_api.py    # end-to-end, against a running server
```
