# Flowgenix user guide

Flowgenix migrates Apache NiFi flows between versions and between **JSON** and
**XML**. You do not need a live NiFi, an API key, or Java — those belong to NiFi
itself when you later import the result.

## What you need

- An exported NiFi flow: a 1.x **template** (`.xml`) or a **flow definition** (`.json`)
  from 1.x or 2.x
- Either the hosted app at https://flowgenix-five.vercel.app/, or Python 3.10+
  to run it yourself

To run it yourself:

```powershell
python -m pip install -r requirements-dev.txt
python -m ui.app
```

Open http://127.0.0.1:7860/.

Nothing is uploaded to a third party and nothing is kept after the request: your
flow is parsed in memory, converted, and handed straight back to the browser,
which turns it into the files you download. On the hosted deployment that also
means a size ceiling — templates and migrated flows must each stay under 4.5 MB.
Run it locally for anything larger.

## Workflow

The settings sit in the left pane and the results on the right (stacked on a
phone). The theme follows your system; **Dark** / **Light** in the top bar
overrides it.

1. Load a flow: drop the file on the source area (or click to browse), paste
   the XML or JSON anywhere on the page, use **Paste text**, or press
   **Load sample flow** for a small NiFi 1.25 template that exercises most
   rules. On the hosted app a file over about 4.3 MB is refused before upload.
2. Flowgenix reports format, detected version, and component count, then runs
   the analysis straight away. Detection uses NAR `bundle.version` stamps. A
   `.json` file is **never** assumed to be 2.x — 1.x has written flow
   definitions since 1.16.
3. Choose source version (pre-filled when detection is certain) and target
   version. Changing either re-runs the analysis.
4. Choose output format:
   - **JSON flow definition** — works for 1.16+ and every 2.x. Import via
     *Upload flow definition* / Registry.
   - **XML template** — only when the target is 1.x. NiFi 2.0 removed templates.
5. **Generate** to produce the converted file plus a report. The downloads
   appear at the top of the Summary tab. The file is named after the process
   group inside it, which is the name NiFi will suggest for the imported group.

## Reading the results

- **Summary** — outcome counts, downloads, version-level concerns (templates
  removed, Variable Registry removed, Java 21) and a table of every component
  that is not simply compatible.
- **Changes** — every rewrite the generator made, grouped by component, shown
  as before → after. Filter by kind (type, property, scheduling, bundle
  version). Components left alone for manual review are listed underneath.
- **Preview** — the source canvas, drawn at NiFi's own coordinates. Amber boxes
  change on migration, red ones are removed or need review, and a red square
  marks an import-readiness fix. Click a process group to open it; the path
  above the canvas takes you back. Scroll or pinch to zoom, drag to pan,
  **Fit** to reset.
- **Fidelity** — appears after Generate. It compares the output with the source
  (counts, names, types, every property and value, positions, connection
  endpoints, dangling references) and reports "N of N checks passed". For a
  template the source is read directly from the XML. Renames listed under
  Changes are expected and not counted as drift.
- **Checklist** — the import steps for this particular flow: resolve the
  flagged components and readiness items, recreate variables as parameters,
  enable services, start. Ticks are saved in your browser for this file and
  target. **Print report** prints Summary, Changes and the Checklist as a
  handover document.
- **Log** — the run log, with **Export logs**.

## Import readiness

The panel under the settings lists problems that exist in the flow itself and
will show up after import on any version:

| Item | Why it matters |
|---|---|
| Sensitive values were not exported | NiFi never exports passwords or keys; re-enter them |
| Absolute file system path | The path must exist on every target node |
| Relationship neither connected nor auto-terminated | NiFi will not start the processor. Checked for XML templates, which list each processor's relationships; JSON flow definitions do not |
| Funnel with no outgoing connection | FlowFiles queue there forever and NiFi flags it invalid |
| Controller service not in this file | The exported group referenced a service defined above it; create it on the target |

Items marked **fix** stop the flow from starting; **check** items are worth
confirming.

## Version dialects (2.6 and XML tags)

NiFi does not keep one schema forever.

**JSON.** Fields that exist only *after* 2.6 (for example `flowStatus` on the
snapshot, `defaultBackoffMechanism` and `maxConcurrentTasks` on a process group)
are dropped when the target is 2.6 or earlier 2.x. When the target is later than
2.6, missing required fields are filled with safe defaults so the importer is
not handed an incomplete document.

**XML.** Older templates used different tag and attribute names
(`encodingVersion` vs `encoding-version`, singular `processGroup` vs
`processGroups`). The parser accepts both. The writer always emits the encoding
that matches the **target** 1.x release (`1.2` before 1.21, `1.3` from 1.21 on).

## What the rules change

Applied automatically when safe:

- NAR bundle versions restamped to the target release
- Known property renames (Jolt `Jolt Transformation DSL` → `Jolt Transform` on 2.x)
- `EVENT_DRIVEN` scheduling → `TIMER_DRIVEN` on 2.x
- Dropping the Variable Registry block when targeting 2.x (values are listed in
  the report; `${var}` expressions are **not** rewritten, because that syntax is
  also valid attribute lookup)

Left unchanged and marked `[MANUAL REVIEW REQUIRED]`:

- Removed processors such as GetHTTP / PostHTTP (suggested replacement: InvokeHTTP)
- Script engines that 2.x dropped (Jython / JRuby on ExecuteScript)

Types no rule covers are copied through untouched and listed as *not verified*
in the report. The flow file itself is not annotated — most processors did not
change between releases, and commenting every one of them would rewrite a flow
that needed no edits.

The migrated file is a faithful copy of the canvas otherwise: identifiers,
positions, properties, property descriptors, `annotationData`, auto-terminated
and retried relationships, connection endpoints, funnels, and the process-group
hierarchy all survive. Processors arrive enabled but not running, exactly as an
import behaves.

## Importing the result

- **2.x JSON:** canvas → *Upload flow definition*, or NiFi Registry.
- **1.x JSON:** same, from NiFi 1.16 onward.
- **1.x XML:** *Upload template* on the 1.x UI.

Review every `[MANUAL REVIEW REQUIRED]` comment before starting processors.
Migrated components are imported stopped. The Checklist tab lists these steps
for your flow.

## Running it elsewhere

```powershell
python -m pytest                       # unit and wiring tests
python tests/smoke_migration_api.py    # end-to-end, against a running server
```

```bash
./script.sh     # container on :7860, with a copy of each migration in ./migrations
vercel --prod   # deploy the same app as one Python function
```

See the README for the deployment layout and the hosted size limits.
