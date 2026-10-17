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

## Use it

**Hosted: https://flowgenix-five.vercel.app/** — nothing to install.

Or run it yourself, which you will want for flows above the 4.5 MB the host
accepts, and for offline or air-gapped work:

```powershell
python -m pip install -r requirements-dev.txt
python -m ui.app
```

Open **http://127.0.0.1:7860/**. Both are the same app, and the steps below
apply to either.

1. Drop a `.xml` template or `.json` flow definition, paste its text anywhere on
   the page, or press **Load sample flow** to try a bundled NiFi 1.25 template.
   The flow is analyzed as soon as it loads.
2. Confirm source version (detected from NAR bundle stamps when present).
3. Pick the target version and output format (JSON, or XML on 1.x targets).
   Changing a version re-runs the analysis.
4. Tick the suggested changes you want in the **Changes** tab. Nothing is
   applied unless you tick it. The **Preview** tab draws the flow as it will be
   with your ticks, and updates as you change them.
5. **Generate**, then download the migrated file and the Markdown/JSON report
   from the Summary tab. The button turns into **Download** until you change a
   tick or a setting.

The download is named after the process group inside the flow, because NiFi
names an imported group after the file it was uploaded from.

The results pane has one tab per question you will have:

| Tab | Shows |
|---|---|
| Summary | Outcome counts, downloads with how to import them, version concerns and every component that needs attention |
| Changes | Suggested changes to tick (upgrade or downgrade), then each rewrite grouped by component, before → after |
| Preview | The canvas at its own coordinates. **Before** shows the source file; **After** shows it with your ticked changes. Click a process group to open it, or a changed processor to see its suggestions |
| Log | The run log. **Export logs** saves it; **Print report** prints Summary, Changes and Import readiness as a handover document |

Alongside the version analysis, the **Import readiness** panel lists what NiFi
will complain about after import regardless of version: sensitive values that
were never exported, absolute file paths, relationships that are neither
connected nor auto-terminated (XML templates), funnels with no way out, and
controller-service references to services outside the file.

## Deploy to Vercel

The app ships as a single Python function. `api/index.py` hands every request to
the same FastAPI app you run locally, and `vercel.json` routes all traffic to it
under the original request path, so the page, the static assets and the API all
come from one place and behave exactly as they do locally.

```powershell
npm i -g vercel
vercel          # preview
vercel --prod   # production
```

A Git-connected project needs no configuration beyond this repo: Vercel reads
`requirements.txt` for the runtime dependencies and `.python-version` for the
interpreter. No environment variables are required. This is how
https://flowgenix-five.vercel.app/ is deployed, straight from `main`.

To check a deployment end to end rather than by eye:

```powershell
$env:FLOWGENIX_BASE_URL="https://flowgenix-five.vercel.app"
python tests/smoke_migration_api.py
```

Two hosting limits are worth knowing before you upload a very large flow:

| Limit | Value | What it means here |
|---|---|---|
| Request body | 4.5 MB | The template you upload must be under it |
| Response body | 4.5 MB | The migrated flow plus the report come back together |
| Function duration | 60 s (`vercel.json`) | Enough for flows of a few thousand components |

The hosted page refuses a file over about 4.3 MB before uploading it (the file
travels inside JSON, which adds a little), and says to run locally instead.
Flows past those limits still migrate fine locally or in Docker, where nothing
travels over a serverless boundary. The run log may also arrive in one burst
rather than line by line, depending on how the platform buffers the response;
the result is identical either way.

## What it changes

Format changes always happen: XML ↔ JSON, NAR bundle versions restamped to the
target, and the JSON fields the target expects.

Component changes are **suggestions**, and none is applied unless you tick it:
replacing a removed or deprecated processor (GetHTTP → InvokeHTTP, PutJMS →
PublishJMS, …), known property renames (Jolt’s `Jolt Transformation DSL` →
`Jolt Transform`), `EVENT_DRIVEN` → `TIMER_DRIVEN` scheduling on 2.x, and the
reverse direction when you migrate down to an older release. A component that
the target rejects and that you did not change is left as it was and marked
`[MANUAL REVIEW REQUIRED]`.

Everything else is copied verbatim, so the imported canvas matches the one you
exported: identifiers, positions, properties and their descriptors,
`annotationData`, relationship flags, connection endpoints, and the
process-group hierarchy. The test suite checks this with `fidelity.py`, which
compares the output with the source component by component. For a template the
source side is read straight from the XML by a separate reader, so a converter
bug cannot hide itself, and the changes you ticked are not counted as drift.
The same checks run from the command line against any file:

```powershell
python tests/check_template_fidelity.py path\to\template.xml 2.6.0
```

## Design

The interface is deliberately plain: IBM Plex Sans and Mono, warm neutrals, one
burnt-orange accent reserved for the primary action, focus and the active tab,
and green / red / amber used only for added / removed / changed. No gradients,
shadows or pill shapes. The rules live in `.cursor/rules/flowgenix-design.mdc`
and `tests/test_design_lint.py` enforces them.

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
python -m pytest                       # unit, fidelity and wiring tests
python tests/smoke_migration_api.py    # end-to-end, against a running server
```

The smoke script also runs the fidelity comparison on the flow the server
returns.
