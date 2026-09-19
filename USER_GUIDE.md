# Flowgenix user guide

Flowgenix migrates Apache NiFi flows between versions and between **JSON** and
**XML**. It runs entirely on your machine. You do not need a live NiFi, a Cursor
API key, or Java — those belong to NiFi itself when you later import the result.

## What you need

- Python 3.10+
- An exported NiFi flow: a 1.x **template** (`.xml`) or a **flow definition** (`.json`)
  from 1.x or 2.x

Install and start:

```powershell
python -m pip install -r requirements.txt
python -m ui.app
```

Open http://127.0.0.1:7860/.

## Workflow

1. Drop the file on the source card (or click to browse).
2. Flowgenix reports format, detected version, and component count. Detection
   uses NAR `bundle.version` stamps. A `.json` file is **never** assumed to be
   2.x — 1.x has written flow definitions since 1.16.
3. Choose source version (pre-filled when detection is certain) and target
   version.
4. Choose output format:
   - **JSON flow definition** — works for 1.16+ and every 2.x. Import via
     *Upload flow definition* / Registry.
   - **XML template** — only when the target is 1.x. NiFi 2.0 removed templates.
5. **Analyze** to see compatibility without writing files.
6. **Generate migrated flow** to download the converted file plus a report.

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
Migrated components are imported stopped.

## Tests and Docker

```powershell
python -m pytest
./script.sh          # container on :7860, artifacts in ./migrations
```
