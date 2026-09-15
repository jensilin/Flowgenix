# NiFi + Cursor Automation — Flow Studio

Turn a plain-English prompt (or an uploaded JSON file) into a real, running Apache
NiFi flow. This repo automates the NiFi REST API directly and ships **Flow
Studio**, a local web UI that uses the Cursor Agent SDK to design and deploy
flows for you — always using the processor types and NAR bundle versions that
actually exist on **your** NiFi instance.

## Two ways to use this repo

| Approach | Best for |
|---|---|
| **Flow Studio UI** (`ui/app.py`) | Prompt-driven flow creation, JSON→CSV uploads, watching the agent work | 
| **Direct scripts** (`create_basic_flow.py`, `build_flow_from_spec.py`) | Scripted/one-shot flows, no Cursor API key needed |

Both talk to NiFi through the same `nifi_client.py` / `nifi_catalog.py` /
`build_flow_from_spec.py`, so every flow — regardless of how it was created —
is built from processor types and NAR bundle versions matched to your NiFi
release.

## Prerequisites

- NiFi 1.25.0 running at `https://127.0.0.1:8443/nifi/` (self-signed TLS is handled for you)
- Python 3.10+
- Windows, macOS, or Linux (a Windows-specific fix for the Cursor SDK is applied automatically — see below)
- The same NiFi username/password you use in the NiFi UI

```powershell
cd C:\Users\ravgs\nifi-cursor-automation
python -m pip install -r requirements.txt
```

## Flow Studio (recommended)

```powershell
python -m ui.app
```

Open **http://127.0.0.1:7860/**.

1. **Cursor API key & model** — collapsible section. Get a key from
   [Cursor Dashboard → API Keys](https://cursor.com/dashboard/api), paste it,
   click **Load models**, pick one (avoid models blocked by your team admin —
   the UI will tell you if one is blocked and suggest an allowed model such as
   `composer-2.5` or `auto`).
2. **NiFi connection + version** — collapsible section. Enter your NiFi
   URL/username/password (kept only in this browser session, never written to
   `.env`) and click **Detect NiFi version**. This loads a full catalog of
   every processor/controller-service type available on your instance, with
   NAR bundle versions — every flow you create afterwards (prompt-based or
   JSON→CSV) is pinned to that release.
3. **JSON → CSV (optional upload)** — toggle this on to reveal the upload
   field. Upload a JSON file, click **Analyze JSON** to infer CSV columns, then
   **Create JSON→CSV flow**. The uploaded file may be a single JSON object, a
   JSON array, or **NDJSON / JSON Lines** (one JSON object per line, as emitted
   by metrics and log pipelines) — line-delimited records are normalized into an
   array before analysis. Messy captures also work: raw REST responses with HTTP
   header or log-timestamp prefixes, objects concatenated without array brackets,
   and files with non-JSON lines mixed in. Every JSON value that can be recovered
   is analyzed and any skipped lines are reported in the run log.

   **Choosing fields.** Analysis lists every inferred field as a tickable chip,
   all ticked by default, with **Select all** / **Clear all** shortcuts. Untick a
   field to leave it out and the exclusion is applied to the flow itself, not
   just the display: the field is removed from the Jolt "Shift" spec and from the
   `CSVRecordSetWriter` schema, so NiFi never emits that column. The excluded
   names are echoed in the run log. The selection also travels with prompt-based
   creation, where the agent is told which fields the user deselected.

   The upload is used to **analyze the schema only**; it is never embedded in the
   flow as sample data. The generated flow moves real files on disk:

   `GetFile → JoltTransformRecord → UpdateAttribute → PutFile`

   `JoltTransformRecord` applies an auto-built "Shift" spec per record through a
   `JsonTreeReader` and writes CSV with a `CSVRecordSetWriter`, so nested fields
   like `customer.name` become `customer_name` with no hand-written Jolt spec,
   and NDJSON input files work as well as JSON arrays. `UpdateAttribute` renames
   the output to `.csv`. Directories default to:

   | Role | Path | Override |
   | --- | --- | --- |
   | GetFile pickup | `data/json-in` | `JSON_TO_CSV_INPUT_DIR` |
   | PutFile output | `data/csv-out` | `JSON_TO_CSV_OUTPUT_DIR` |

   Both are created automatically, and your upload is copied into the pickup
   directory so the flow produces a CSV as soon as it starts. GetFile consumes
   files it reads, which is the normal pickup-directory contract — drop more
   JSON/NDJSON files in there any time and CSV appears in the output directory.
   Because NiFi resolves these paths itself, override them when NiFi runs
   somewhere that cannot see this repo (for example in a container).

   On older NiFi without `JoltTransformRecord`, the flow falls back to
   `GetFile → JoltTransformJSON → ConvertRecord → UpdateAttribute → PutFile`;
   note that `JoltTransformJSON` needs one whole JSON document per file, so
   NDJSON input is not supported on that path.
4. **Flow prompt** — describe any flow in plain English, or click one of the
   example prompt chips, then **Create flow from prompt**. A local Cursor
   agent designs the flow JSON and runs `build_flow_from_spec.py` against your
   NiFi for you, streaming its progress into the run log.

   Every prompt automatically gets this standing requirement appended if you
   didn't write it yourself, so you never have to remember it:

   > Create, configure, connect, validate, and deploy the complete flow
   > automatically using the NiFi REST API.

   The run log tells you when it was added. It is skipped when your prompt
   already asks to deploy via the REST API. The agent is also required to verify
   the deployment afterwards — processor and controller-service validation state
   plus bulletins — because a flow can deploy successfully yet never run (a
   controller service stuck in ENABLING stalls everything silently).
5. **Prompt assistant (chat bot)** — the floating button at the bottom right.
   Describe the scenario in your own words ("poll an HTTP API every 5 minutes,
   keep only failed orders, drop them into Kafka") and the bot replies with a
   ready-to-run flow prompt built from the processors your NiFi version actually
   has. Each answer carries two buttons: **Use this prompt** drops it straight
   into the Flow prompt box, and **Copy** puts it on the clipboard.

   It reuses the Cursor API key from the top of the page but has its **own model
   dropdown**, filled by the same **Load models** button — so you can draft with
   a cheap fast model and build with a stronger one. The conversation keeps
   context, so you can refine ("make it every 30 seconds and add a failure
   route") and get an updated prompt back.

   Detecting your NiFi version first makes the suggestions concrete, since the
   bot is given the live catalog and told to name only installed types. Without
   it the bot still answers, but says the processor names are unverified. If you
   have a JSON file loaded, its inferred columns and the GetFile/PutFile
   directories are handed to the bot too, so conversion prompts come out
   correctly wired. The bot only writes text — it never creates files, runs
   commands, or touches the NiFi API. Ask a plain question ("which Kafka
   processors do I have?") and you just get an answer with no prompt attached.
6. **Run log** — color-coded by type (status, agent output, agent thinking,
   tool calls, results, errors). Click **Export logs** any time to download a
   timestamped `.txt` transcript of the whole run for reference.

   **Flow test results** appear in a table at the end of the log once a flow is
   deployed. Rather than trusting the agent's own summary, `flow_tests.py`
   interrogates the deployed process group over the REST API and reports a
   pass/fail row per test case, each with a short description of what it checks:

   | Test case | What it proves |
   | --- | --- |
   | Process group created | The group exists on the canvas |
   | Processors created | Every processor in the spec exists, including in sub-groups |
   | Processor configuration valid | No processor reports a validation error |
   | Processors running | Processors reached the RUNNING state |
   | Connections wired | Flow files have a path through the flow |
   | Controller services enabled | Readers/writers are ENABLED and valid |
   | No error bulletins | NiFi logged no runtime ERROR/WARNING for the group |
   | Data processed | At least one flow file actually moved |
   | No stuck queue | Nothing is piling up in a connection queue |
   | Output file written | A real file landed in the destination directory |

   A row is **skipped** when it does not apply — services when the flow declares
   none, or the runtime checks when the spec asked for a stopped group — and
   **warned** when something is suspicious but legitimate, such as a source with
   nothing to read yet. Failures also appear in the log, and the whole table is
   included in **Export logs**. Cases that cannot apply are never silently
   counted as passes, so a green run means the flow really is live.
7. **Token usage by model** — the bottom section tallies what each model cost
   you. After every flow build and every prompt assistant reply, the token usage
   the Cursor SDK reports for that run is added to a per-model row: input,
   output, cache read/write, reasoning, and total tokens, plus the charged
   amount when the backend reports cost. Each row also shows how the model was
   used (for example `3× flow, 5× assistant`) and when it last ran, so you can
   see whether your spend is going to flow building or to drafting prompts.

   The assistant reports its own cost in two more places so you don't have to
   scroll down to see it: each reply carries a token count under it (hover for
   the input/output/cache breakdown), and the pill in the chat header keeps a
   running total for the session.

   Totals are written to `data/token-usage.json` (override with
   `FLOW_STUDIO_USAGE_FILE`), so they survive server restarts and are shared
   across browser tabs. **Refresh** re-reads them, **Reset** clears them to
   start a fresh measurement. A run that reports no usage — which some models
   and cached replies do — is called out in the run log and leaves the table
   untouched rather than logging a zero.
8. Refresh `https://127.0.0.1:8443/nifi/` to see the flow, or use
   **Download generated JSON** to grab the exact spec that was deployed.

### What the agent can build

Flows aren't limited to a flat list of processors — the agent can use NiFi's
full structural toolkit when it fits the request:

**Components**

- **Every processor and controller service installed on the instance** — 359
  processors and 123 services on a stock NiFi 1.25.0 — is offered to the agent,
  not a curated subset. Specialised components (database, SFTP, S3, Kafka,
  MQTT, Elasticsearch, scripting, the full record family) are all fair game.
- Types are pinned to the NAR bundle for the detected NiFi version, and a type
  that isn't installed is rejected rather than deployed broken.
- Nothing is hardcoded to one release. The version, the component catalog, the
  bundle versions, and which tuning fields are available are all read from the
  live instance, so pointing `.env` at a different NiFi adapts automatically.
  Fields the detected release doesn't support are skipped and reported under
  `warnings` instead of failing the deploy.

**Canvas structure**

- **Nested sub-process groups** (recursively) to organize a complex flow into
  logical stages.
- **Input/output ports** to link a parent flow to a sub-process-group's
  contents (cross-group connections), or link between top-level flows. A port
  can be made a public **site-to-site** port with `allowRemoteAccess`.
- **Funnels** to merge multiple connections into one.
- **Labels** to annotate the canvas.
- **Remote process groups** for site-to-site transfer to another NiFi.
- Controller services declared in a parent group are automatically visible to
  processors in nested child groups.

**Scheduling and runtime tuning**

- **CRON scheduling** (`schedulingStrategy` + a quartz `schedulingPeriod`) for
  clock-based runs, alongside timer-driven periods.
- **Concurrent tasks**, **run duration**, and `executionNode: PRIMARY` for
  run-once-per-cluster sources.
- **Penalty/yield duration** and **bulletin level** per processor.
- **Retry on a relationship** (`retryCount`, `retriedRelationships`,
  `backoffMechanism`, `maxBackoffPeriod`) instead of routing failures onward.

**Queues**

- **Back pressure** thresholds (object count and data size) and **FlowFile
  expiration** per connection.
- **Prioritizers** (e.g. oldest-first, newest-first, priority attribute).
- **Load balancing** strategy, partition attribute, and compression for
  clusters.
- Group-level defaults that seed every connection in the group.

**Configuration and controller scope**

- **Parameter contexts**: declare values once, attach a context to a group, and
  reference them anywhere as `#{paramName}`.
- **Reporting tasks** and **controller-level services**, both reused by name so
  a redeploy doesn't stack up duplicates.
- **Process group settings**: comments, FlowFile concurrency, and outbound
  policy.

**Rebuilding**

- `replaceExisting` stops a same-named group, disables its services, drains its
  queues, and deletes it before rebuilding — so asking for a change to an
  existing flow updates it instead of leaving a second copy on the canvas.

See `agent_prompt.py` (`SPEC_SCHEMA`) for the exact JSON shape, or ask for a
flow like:

> Create a process group with a sub-process-group for validation, linked in
> via input/output ports, that merges two branches with a funnel before
> logging.

> Poll the API every night at 2am on the primary node only, with 4 concurrent
> tasks, retry the failure relationship 3 times, and cap the queue at 5000
> files with oldest-first priority.

## Configuration

Everything works out of the box; these environment variables are for when the
defaults don't fit.

| Variable | Default | Purpose |
|---|---|---|
| `FLOW_STUDIO_HOST` | `127.0.0.1` | Interface Flow Studio binds to. The `Dockerfile` sets `0.0.0.0` so the container is reachable. |
| `FLOW_STUDIO_PORT` | `7860` | Flow Studio port. |
| `FLOW_STUDIO_RELOAD` | off | Set to `1` to auto-reload on code changes. Without it, a long-running server keeps serving the code it imported at startup, so edits to the flow-building modules appear to have no effect. |
| `FLOW_STUDIO_USAGE_FILE` | `data/token-usage.json` | Where per-model token totals are stored. Point it elsewhere to keep separate tallies, e.g. per environment. |
| `JSON_TO_CSV_INPUT_DIR` | `data/json-in` | GetFile pickup directory for JSON→CSV flows. |
| `JSON_TO_CSV_OUTPUT_DIR` | `data/csv-out` | PutFile output directory for converted CSV. |
| `NIFI_URL` / `NIFI_USERNAME` / `NIFI_PASSWORD` | — | Read from `.env` by the direct scripts. Flow Studio takes these from the browser form instead. |

The two `JSON_TO_CSV_*` paths are resolved by the **NiFi process**, not by this
repo, so they must be absolute paths NiFi itself can see. Override them when
NiFi runs somewhere that cannot reach this directory, such as in a container.

## Run Flow Studio with Docker

A `Dockerfile` and launch script (`script.sh`) are included if you'd rather
not install Python dependencies locally. The container runs only Flow Studio
— **not** NiFi itself, so point it at an existing NiFi instance (e.g. one
running on your host machine).

```bash
chmod +x script.sh   # first time only (not needed if already executable)
./script.sh          # builds the image and starts the container on :7860
./script.sh logs     # tail the container logs
./script.sh stop     # stop and remove the container
```

On Windows, run it from WSL or Git Bash (`bash script.sh`), or use
`docker build` / `docker run` directly — see `Dockerfile`.

Open **http://localhost:7860/** as usual. Since `127.0.0.1` inside the
container refers to the container itself (not your host machine), enter your
NiFi URL as **`https://host.docker.internal:8443`** instead of
`https://127.0.0.1:8443` when NiFi runs on your host — `script.sh` already
adds the `host.docker.internal` mapping needed for this to resolve on Linux
(Docker Desktop on Mac/Windows provides it automatically).

Generated flow specs are bind-mounted to `./flows` on your host, so they
persist across container restarts and are easy to inspect outside Docker.

Override the port with `FLOW_STUDIO_PORT=8080 ./script.sh`.

## Direct scripts (no Cursor API key needed)

### 1. Put credentials in `.env`

```powershell
copy .env.example .env
notepad .env
```

```env
NIFI_URL=https://127.0.0.1:8443
NIFI_USERNAME=your-nifi-username
NIFI_PASSWORD=your-nifi-password
```

If you forgot the generated single-user password, check the NiFi startup
console / `logs\nifi-app.log` for the generated credentials (first start), or
reset via NiFi docs for single-user mode.

### 2. Create the basic demo flow

```powershell
python create_basic_flow.py
```

This creates process group **`cursor-basic-demo`** with:

`GenerateFlowFile` (every 5s, payload `hello-from-cursor`) → `LogAttribute`

### 3. Build a flow from a hand-written spec

```powershell
python build_flow_from_spec.py flows/templates/csv_to_json.json
```

`build_flow_from_spec.py` resolves every processor/service `type` + `bundle`
against your NiFi's own catalog (rejecting anything not installed), creates
controller services, processors, ports, funnels, labels, and nested
sub-process-groups, wires up connections (including across group boundaries
via ports), and starts everything.

#### Property names and values

NiFi's REST API only accepts a property's *internal* name and a value's *stored*
value, but its UI and docs show friendlier display labels. Specs are normalized
against each component's live descriptors, so either form works:

| You can write | NiFi receives |
|---|---|
| `"Jolt Specification"` (display name) | `jolt-record-spec` (internal name) |
| `"Shift"` (allowable-value label) | `jolt-transform-shift` (stored value) |
| `"Schema Access Strategy"` | `schema-access-strategy` |

Keys the component doesn't declare are dropped, which stops an invented property
name from leaving the component permanently invalid. **Because of that,
user-defined properties must go under `dynamicProperties`** — they aren't in any
descriptor, so putting them in `properties` means losing them:

```json
{
  "name": "NameOutputCSV",
  "type": "org.apache.nifi.processors.standard.UpdateAttribute",
  "properties": {},
  "dynamicProperties": {
    "filename": "${filename:substringBeforeLast('.')}.csv"
  }
}
```

This applies to anything configured through user-defined entries — the
attributes `UpdateAttribute` sets, dynamic properties on controller services,
and so on.

### 4. Ask Cursor directly in chat

Once `.env` works, you can also skip the UI and just ask in chat:

> Create a NiFi process group `demo-2` with GenerateFlowFile → UpdateAttribute → LogAttribute

The agent follows `.cursor/rules/nifi-automation.mdc` and reuses the same
helpers.

## How NiFi version awareness works

`nifi_catalog.load_catalog()` calls `/nifi-api/flow/about` to detect the
running NiFi version, then lists every installed processor and controller
service type via `/nifi-api/flow/processor-types` and
`/nifi-api/flow/controller-service-types` — including each type's NAR bundle
(`group`/`artifact`/`version`). This is written to `flows/.nifi-catalog.json`
and used by:

- `build_flow_from_spec.py` — resolves each spec's processor/service `type` to
  the installed type + matching bundle before creating it; rejects unknown types.
- `json_to_csv.py` — picks the right JSON→CSV strategy for your version
  (`jolt_record` when `JoltTransformRecord` is available, then
  `jolt_convert_record` for `JoltTransformJSON`, falling back to
  `convert_record` or legacy processors on older releases).
- `agent_prompt.py` — feeds the full catalog (types + bundle versions +
  capability strategies) to the Cursor agent so it never guesses a processor
  class name or NAR version that doesn't exist on your instance.

This matters because processor class names and NAR bundle versions differ
across NiFi releases — a flow built for 1.24 may reference types that don't
exist (or have different bundle versions) on 1.25, and vice versa.

## Project layout

| File | Role |
|------|------|
| `nifi_client.py` | REST client: login/CSRF; create process groups, processors, controller services, ports, funnels, labels, connections, parameter contexts, reporting tasks, remote process groups; group settings; start/stop, queue draining, and delete |
| `nifi_catalog.py` | Detect NiFi version; catalog processor/service types + NAR bundle versions; pick the JSON→CSV strategy |
| `json_analyze.py` | Parse JSON/NDJSON/messy captures; infer CSV columns; auto-build the Jolt "Shift" specs (per-record and array) |
| `json_to_csv.py` | Version-aware `GetFile → flatten → PutFile` JSON→CSV flow spec; owns the pickup/output directories |
| `agent_prompt.py` | System prompt, JSON flow-spec schema, and the standing deploy-and-validate requirement appended to every prompt |
| `prompt_assistant.py` | Chat bot that turns a plain-English scenario into a flow prompt using the live catalog; extracts the prompt from the reply |
| `usage_tracker.py` | Per-model token/cost totals recorded after every agent run; backs the "Token usage by model" section |
| `flow_tests.py` | Post-deployment test suite: validates the deployed group over the REST API and returns pass/fail rows |
| `build_flow_from_spec.py` | Recursively builds a flow (nested process groups, ports, funnels, labels, remote groups) from a JSON spec; applies scheduling/retry/queue tuning and parameter contexts; normalizes property names/values against live descriptors |
| `create_basic_flow.py` | One-shot basic demo flow |
| `ui/app.py` | Flow Studio FastAPI backend (Cursor SDK agent runs, NiFi inspect, JSON analyze/convert) |
| `ui/win_bridge_patch.py` | Fixes a Windows `select()` incompatibility in the Cursor SDK's local agent bridge |
| `ui/static/` | Flow Studio frontend (Nokia white/blue theme, collapsible sections, colorized log, export) |
| `samples/orders.json` | Sample nested JSON for testing JSON→CSV |
| `samples/metrics.ndjson` | Sample NDJSON / JSON Lines (metrics) for testing JSON→CSV |
| `flows/` | Generated flow specs + `.nifi-catalog.json` (auto-written catalog) |
| `flows/templates/` | Hand-written example specs |
| `data/json-in` | GetFile pickup directory for JSON→CSV flows (auto-created, git-ignored) |
| `data/csv-out` | PutFile output directory where converted CSV lands (auto-created, git-ignored) |
| `data/token-usage.json` | Per-model token totals (auto-created, git-ignored) |
| `.env` / `.env.example` | Local NiFi secrets for direct scripts (never committed) |
| `.cursor/rules/nifi-automation.mdc` | How any Cursor agent (chat or Flow Studio) should automate NiFi in this repo |
| `Dockerfile` | Container image for Flow Studio (NiFi is not included) |
| `script.sh` | Build + run/stop/logs helper for the Docker container |

## Security note

Local HTTPS uses NiFi's self-signed cert; the client disables TLS verify for
localhost only — do not reuse this pattern against production without proper
trust stores. Cursor API keys and NiFi credentials entered in Flow Studio are
kept in the browser session (or `sessionStorage` if you check "remember")
only, never written to disk. Keep `.env` (used only by the direct scripts) out
of version control.
