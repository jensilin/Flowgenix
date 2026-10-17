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
5. Open **Changes** and tick the suggested changes you want. None is ticked to
   start with, and nothing is applied unless you tick it. **Preview** shows the
   flow with your ticks before you generate anything.
6. **Generate** to produce the converted file plus a report. The downloads
   appear at the top of the Summary tab, with a line on how to import the file.
   The file is named after the process group inside it, which is the name NiFi
   will suggest for the imported group. Until you change a tick or a setting,
   the Generate button downloads the same file again.

## Reading the results

- **Summary** — outcome counts, downloads, version-level concerns (templates
  removed, Variable Registry removed, Java 21) and a table of every component
  that is not simply compatible.
- **Changes** — first the **suggested changes**, grouped by component: replace
  a removed or deprecated processor, rename a property, change scheduling, or
  the reverse when you migrate down. Each shows whether the target requires it
  and what it rewires. Tick the ones you want, or use **Accept all** / **Clear**.
  **Show in preview** opens that component on the canvas. After Generate, every
  rewrite the generator made is listed underneath as before → after, filterable
  by kind, with the components left alone for manual review.
- **Preview** — the canvas, drawn at NiFi's own coordinates. The dropdown above
  it switches between:
  - **Before: source file** — the flow as uploaded. Amber boxes change on
    migration, red ones are removed or need review.
  - **After: N of M ticked changes** — the flow as Generate will write it with
    your current ticks. Green boxes have a ticked change applied (a replaced
    processor shows its new type and "replaced, was …", and renamed
    relationships are highlighted). Amber means an optional change is not
    ticked; red means a required change is not ticked or the component still
    needs review. It redraws as you tick, before and after Generate.

  Click a process group to open it; the path above the canvas takes you back.
  Click a changed processor to jump to its suggestions. Scroll or pinch to
  zoom, drag to pan, **Fit** to reset. The zoom stays put when you switch tabs.
- **Log** — the run log, with **Export logs**, and **Print report**, which
  prints Summary, Changes and Import readiness as a handover document.

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

Always applied, because the target format needs them:

- XML ↔ JSON conversion and the target's JSON fields
- Apache NAR bundle versions restamped to the target release. Third-party NARs
  keep their own version, since they are released separately; make sure a build
  of each one is installed on the target.
- Dropping the Variable Registry block when targeting 2.x (values are listed in
  the report; `${var}` expressions are **not** rewritten, because that syntax is
  also valid attribute lookup)

Suggested, and applied only when you tick them in Changes:

- Replacing removed or deprecated processors (GetHTTP / PostHTTP → InvokeHTTP,
  HashContent → CryptographicHashContent, PutJMS → PublishJMS, …), with their
  properties and relationships carried across
- Known property renames (Jolt `Jolt Transformation DSL` → `Jolt Transform` on 2.x)
- `EVENT_DRIVEN` scheduling → `TIMER_DRIVEN` on 2.x
- The reverse of these when the target is older than the source

Left unchanged and marked `[MANUAL REVIEW REQUIRED]` when you do not tick a
replacement, or when there is none:

- Removed processors such as GetHTTP / PostHTTP
- Script engines that 2.x dropped (Jython / JRuby on ExecuteScript)

Types no rule covers are copied through untouched and listed as *not verified*
in the report. The flow file itself is not annotated — most processors did not
change between releases, and commenting every one of them would rewrite a flow
that needed no edits.

The migrated file is a faithful copy of the canvas otherwise: identifiers,
positions, properties, property descriptors, `annotationData`, scheduling and
concurrency settings, styles, auto-terminated and retried relationships,
connection endpoints, bends, prioritizers and load balancing, ports, funnels,
labels, remote process groups, and the process-group hierarchy all survive, in
JSON and in XML. An XML template for an older 1.x release leaves out settings
that release did not have (retry before 1.16, load balancing before 1.8).

You can migrate a file Flowgenix already migrated. Its earlier provenance note is
replaced rather than repeated. A `[MANUAL REVIEW REQUIRED]` banner from the earlier
run is removed when the new target does not need review, and the Changes tab
lists that as *note removed*. Processors arrive enabled but not running, exactly as an
import behaves. The test suite checks this with a separate comparison
(`fidelity.py`); `python tests/check_template_fidelity.py your-file.xml 2.6.0`
runs it on any file.

## Importing the result

- **2.x JSON:** canvas → *Upload flow definition*, or NiFi Registry.
- **1.x JSON:** same, from NiFi 1.16 onward.
- **1.x XML:** *Upload template* on the 1.x UI.

Before starting processors, review every `[MANUAL REVIEW REQUIRED]` comment,
check the settings of any processor you replaced, and fix the items in the
Import readiness panel. Migrated components are imported stopped: enable the
controller services first, then start the processors.

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
