# NiFi Flow Studio — Complete User Guide

This is the end-to-end guide for the **NiFi + Cursor Automation** project ("Flow
Studio"). It starts from an empty machine — Java, NiFi, Python — and walks all
the way through building flows from a prompt, converting JSON to CSV, migrating a
flow between NiFi versions, and running the whole thing in Docker.

If you have never touched NiFi before, read from the top. If NiFi is already
running and you just want the app, jump to [4. Install Flow Studio](#4-install-flow-studio).

---

## Table of contents

1. [What this application does](#1-what-this-application-does)
2. [Concepts and glossary](#2-concepts-and-glossary-read-once)
3. [Prerequisites: Java (JDK) and Apache NiFi](#3-prerequisites-java-jdk-and-apache-nifi)
   - [3.1 Install a JDK](#31-install-a-jdk)
   - [3.2 Install and start NiFi](#32-install-and-start-nifi)
   - [3.3 Find your NiFi login](#33-find-your-nifi-login)
4. [Install Flow Studio](#4-install-flow-studio)
5. [Configuration](#5-configuration)
6. [Running Flow Studio (the web UI)](#6-running-flow-studio-the-web-ui)
7. [The UI, section by section](#7-the-ui-section-by-section)
   - [7.1 Cursor API key & model](#71-cursor-api-key--model)
   - [7.2 NiFi connection + version](#72-nifi-connection--version)
   - [7.3 Build a flow from a prompt](#73-build-a-flow-from-a-prompt)
   - [7.4 JSON → CSV from an upload](#74-json--csv-from-an-upload)
   - [7.5 Prompt assistant (chat bot)](#75-prompt-assistant-chat-bot)
   - [7.6 Run log and flow test results](#76-run-log-and-flow-test-results)
   - [7.7 Token usage by model](#77-token-usage-by-model)
8. [NiFi version migration](#8-nifi-version-migration)
9. [What the agent can build](#9-what-the-agent-can-build)
10. [Direct scripts (no Cursor API key)](#10-direct-scripts-no-cursor-api-key)
11. [Writing a flow spec by hand](#11-writing-a-flow-spec-by-hand)
12. [How version awareness works](#12-how-version-awareness-works)
13. [Running in Docker](#13-running-in-docker)
14. [Testing the project](#14-testing-the-project)
15. [Troubleshooting](#15-troubleshooting)
16. [Security notes](#16-security-notes)
17. [File and directory reference](#17-file-and-directory-reference)

---

## 1. What this application does

Flow Studio turns intent into a **real, running Apache NiFi flow**. You describe
what you want — in plain English, or by uploading a data file — and the tool
designs the flow, wires it up, deploys it to your NiFi over the REST API, and
then verifies that data actually moved.

It does three big jobs:

| Job | What you give it | What you get back |
|---|---|---|
| **Build from a prompt** | A sentence, e.g. *"poll an API every 5 min and drop failures into Kafka"* | A deployed, running process group on your NiFi canvas |
| **JSON → CSV** | A JSON / NDJSON file | A deployed `GetFile → flatten → PutFile` flow that writes CSV |
| **Migrate a version** | An exported NiFi flow/template | A compatibility report + a flow definition valid for the target NiFi version |

The one rule that makes it trustworthy: **nothing about your NiFi is hardcoded.**
The tool reads the running instance to learn its version, its installed
processor and controller-service types, and the exact NAR bundle versions, then
builds only with components that genuinely exist on *your* release.

There are two front doors:

- **Flow Studio UI** (`ui/app.py`) — the web app; needs a Cursor API key for the
  prompt-driven features.
- **Direct scripts** (`create_basic_flow.py`, `build_flow_from_spec.py`) — no
  Cursor key needed; you hand-write or generate the flow spec.

---

## 2. Concepts and glossary (read once)

If NiFi terms are new to you, this section makes the rest of the guide readable.

- **JDK / Java** — NiFi is a Java application, so a Java runtime (JDK) must be
  installed *for NiFi*. Flow Studio itself is Python and does **not** need Java.
  NiFi **1.x** runs on **Java 8 or 11**; NiFi **2.x** requires **Java 21**.
- **Apache NiFi** — the data-flow tool this project automates. It has a web UI
  (the "canvas") and a REST API. Flow Studio talks to the REST API.
- **Processor** — a single work unit on the canvas (e.g. `GetFile`,
  `InvokeHTTP`, `PutFile`). Flows are processors connected together.
- **Controller service** — shared, reusable configuration used by processors
  (e.g. a `JsonTreeReader` record reader, a `CSVRecordSetWriter`, a DB
  connection pool). Must be **ENABLED** before the processors that use it start.
- **FlowFile** — one unit of data (content + attributes) moving through the flow.
- **Relationship** — a labelled outcome of a processor (`success`, `failure`,
  …). Every relationship must either be connected onward or **auto-terminated**,
  or the processor is invalid.
- **Process group** — a folder/box on the canvas that holds processors and can
  nest sub-groups. This project creates a new process group per flow so the
  canvas stays tidy.
- **Port (input/output)** — how FlowFiles cross a process-group boundary.
- **Funnel** — merges many connections into one.
- **Parameter context** — named values you set once and reference anywhere as
  `#{name}`.
- **NAR bundle** — the packaged library a processor type ships in, identified by
  `group` / `artifact` / `version`. The bundle version differs between NiFi
  releases, which is why version awareness matters.
- **Back pressure** — per-connection limits (object count / data size) that pause
  an upstream processor when a queue fills up.
- **Template (1.x) vs. flow definition (JSON)** — NiFi 1.x could export an XML
  **template**; NiFi 2.0 removed templates. Both 1.x and 2.x export a JSON
  **flow definition** (`VersionedFlowSnapshot`). The migration feature converts
  the old XML into the new JSON.
- **Cursor API key** — authorizes the local Cursor SDK agent that designs flows
  from your prompts. Only the prompt-driven features need it.

---

## 3. Prerequisites: Java (JDK) and Apache NiFi

You need a NiFi instance for Flow Studio to talk to. If you already have one
running, note its URL and login and skip to [section 4](#4-install-flow-studio).

### 3.1 Install a JDK

Pick the Java version that matches the NiFi line you intend to run:

| NiFi line | Required Java | Notes |
|---|---|---|
| NiFi 1.x (e.g. 1.25.0, 1.28.1) | **Java 8 or 11** | The most common local setup |
| NiFi 2.x (e.g. 2.0.0 – 2.11.0) | **Java 21** | NiFi 2.0 dropped Java 8/11 support |

Any distribution works — [Eclipse Temurin (Adoptium)](https://adoptium.net/),
Oracle JDK, Microsoft Build of OpenJDK, or Amazon Corretto.

**Windows install and verify (PowerShell):**

```powershell
# After installing a JDK, point JAVA_HOME at it (adjust the path to your JDK):
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-11.0.24.7-hotspot"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"

java -version   # should print the version you installed
```

To make `JAVA_HOME` permanent, set it in **System Properties → Environment
Variables** (or `setx JAVA_HOME "..."` in an admin shell, then reopen the shell).

> If `java -version` prints a version that does **not** match your NiFi line,
> NiFi will fail to start with a Java-version error. Fix `JAVA_HOME` first.

### 3.2 Install and start NiFi

1. Download a NiFi binary from the [Apache NiFi downloads
   page](https://nifi.apache.org/download/) — the `*-bin.zip` (Windows) or
   `*-bin.tar.gz` (macOS/Linux).
2. Unzip it somewhere without spaces in the path if you can (e.g. `C:\nifi\`).
3. Start it:

   **Windows (PowerShell):**
   ```powershell
   cd C:\nifi\nifi-1.25.0
   .\bin\run-nifi.bat
   ```

   **macOS / Linux:**
   ```bash
   cd ~/nifi/nifi-1.25.0
   ./bin/nifi.sh start
   ```

4. NiFi takes a minute or two to come up. It serves its UI over **HTTPS with a
   self-signed certificate** at a URL like:

   ```
   https://127.0.0.1:8443/nifi/
   ```

   Your browser will warn about the certificate — that is expected for a local
   single-user instance; accept it and continue. Flow Studio handles the
   self-signed cert automatically.

### 3.3 Find your NiFi login

Modern NiFi runs in **single-user** mode and generates a random username and
password on first start. To find or reset them:

- **First-start credentials** are printed to the startup console and to
  `logs/nifi-app.log` — search that file for `Generated Username` and
  `Generated Password`.
- **To set your own** single-user credentials:

  ```bash
  # from the NiFi install directory
  ./bin/nifi.sh set-single-user-credentials <username> <password>   # macOS/Linux
  .\bin\nifi.bat set-single-user-credentials <username> <password>  # Windows
  ```

  The password must be at least 12 characters. Restart NiFi after setting it.

> **Authentication disabled?** Some older installs run on plain HTTP with no
> login. Flow Studio detects this: leave username/password blank and it will
> confirm the API answers without a token before proceeding.

Keep three things handy for the next step: your **NiFi URL**, **username**, and
**password**.

---

## 4. Install Flow Studio

### 4.1 Install Python

- **Python 3.10 or newer** (this repo has been run on 3.11 and 3.13).
- Verify:
  ```powershell
  python --version
  ```

### 4.2 Install the Python dependencies

Flow Studio depends on the following packages:

| Package | Purpose |
|---|---|
| `cursor-sdk` | Runs the local Cursor agent that designs flows from prompts |
| `fastapi` | The web backend |
| `uvicorn` | The ASGI server that serves the UI |
| `requests` | Talks to the NiFi REST API |
| `python-dotenv` | Reads `.env` for the direct scripts |
| `pydantic` | Request validation |
| `python-pptx`, `Pillow` | Only needed for the demo-deck builder (`build_demo_deck.py`); optional for normal use |

Install them:

```powershell
cd C:\Users\ravgs\nifi-cursor-automation
python -m pip install cursor-sdk fastapi uvicorn requests python-dotenv pydantic
# Optional, only if you plan to build the demo PowerPoint deck:
python -m pip install python-pptx Pillow
```

> **Note on `requirements.txt`:** the README and `Dockerfile` reference a
> `requirements.txt`, but it is not currently checked into the repo. The
> `pip install` line above installs the same set. If you want the documented
> file, create `requirements.txt` with those package names (one per line) — the
> Docker build expects it.

---

## 5. Configuration

Everything works out of the box; these are only for when the defaults don't fit.

### 5.1 `.env` (for the direct scripts only)

The **UI takes NiFi credentials from the browser form**, not from `.env`. The
**direct scripts** (`create_basic_flow.py`, `build_flow_from_spec.py`) read
`.env`:

```powershell
copy .env.example .env
notepad .env
```

```env
NIFI_URL=https://127.0.0.1:8443
NIFI_USERNAME=your-nifi-username
NIFI_PASSWORD=your-nifi-password
```

`.env` is git-ignored — keep it out of version control.

### 5.2 Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLOW_STUDIO_HOST` | `127.0.0.1` | Interface the UI binds to. The Docker image sets `0.0.0.0`. |
| `FLOW_STUDIO_PORT` | `7860` | UI port. |
| `FLOW_STUDIO_RELOAD` | off | Set to `1` to auto-reload on code edits during development. |
| `FLOW_STUDIO_USAGE_FILE` | `data/token-usage.json` | Where per-model token totals are stored. |
| `JSON_TO_CSV_INPUT_DIR` | `data/json-in` | `GetFile` pickup directory for JSON→CSV flows. |
| `JSON_TO_CSV_OUTPUT_DIR` | `data/csv-out` | `PutFile` output directory for converted CSV. |
| `NIFI_URL` / `NIFI_USERNAME` / `NIFI_PASSWORD` | — | Read from `.env` by the direct scripts. |

> The two `JSON_TO_CSV_*` paths are resolved by the **NiFi process**, not by
> Flow Studio. They must be paths NiFi itself can see. Override them when NiFi
> runs somewhere that can't reach this repo (a container, WSL, another host).

---

## 6. Running Flow Studio (the web UI)

```powershell
cd C:\Users\ravgs\nifi-cursor-automation
python -m ui.app
```

Then open **http://127.0.0.1:7860/**.

To use a different port:

```powershell
$env:FLOW_STUDIO_PORT = "8080"
python -m ui.app
```

> **Windows note:** the Cursor SDK's local agent uses a `select()` call that
> behaves differently on Windows. Flow Studio applies a compatibility patch
> automatically at startup (`ui/win_bridge_patch.py`) — you don't need to do
> anything, but that's what the patch is for.

The typical first-run order in the UI is: **API key → Load models → NiFi
connection → Detect NiFi version → build something.**

---

## 7. The UI, section by section

The page is a set of collapsible sections. Top to bottom:

### 7.1 Cursor API key & model

1. Get a key from the [Cursor Dashboard → API
   Keys](https://cursor.com/dashboard/api).
2. Paste it into **Cursor API key**.
3. Click **Load models** and pick one from the dropdown.

The key is kept **in the browser session only** (or `sessionStorage` if you tick
"remember") — it is never written to disk by the server. If your team admin has
blocked a model, the UI tells you and suggests an allowed one (such as
`composer-2.5` or `auto`).

### 7.2 NiFi connection + version

1. Enter your **NiFi URL**, **username**, and **password** (from
   [section 3.3](#33-find-your-nifi-login)). These stay in the browser session,
   never in `.env`.
   - You may paste the UI address (`.../nifi/` on 1.x, `.../nf/` on 2.x) — the
     client reduces it to the REST API root for you.
   - Leave username/password blank if the instance has authentication disabled.
2. Click **Detect NiFi version**.

This loads a **full catalog** of every processor and controller-service type on
your instance, with NAR bundle versions, and pins every flow you build afterward
to that release. **Do this before building anything** — it's what makes the
generated flows valid.

### 7.3 Build a flow from a prompt

1. Type a description in **Flow prompt**, or click an example chip.
2. Click **Create flow from prompt**.

A local Cursor agent designs the flow JSON and runs `build_flow_from_spec.py`
against your NiFi, streaming progress into the run log.

Two things happen automatically:

- If you didn't say so yourself, this standing requirement is appended to your
  prompt (the run log tells you when):
  > *Create, configure, connect, validate, and deploy the complete flow
  > automatically using the NiFi REST API.*
- The agent is required to **verify the deployment** afterward (validation state
  + bulletins), because a flow can deploy yet never run.

When it finishes, refresh your NiFi canvas to see the flow, or use **Download
generated JSON** to grab the exact spec that was deployed.

**Example prompts:**

> Create a process group with a sub-process-group for validation, linked via
> input/output ports, that merges two branches with a funnel before logging.

> Poll the API every night at 2am on the primary node only, with 4 concurrent
> tasks, retry the failure relationship 3 times, and cap the queue at 5000 files
> with oldest-first priority.

### 7.4 JSON → CSV from an upload

1. Toggle **JSON → CSV (optional upload)** on.
2. Upload a file — a single JSON object, a JSON array, or **NDJSON / JSON
   Lines** (one object per line). Messy captures work too: REST responses with
   header/log prefixes, objects concatenated without array brackets, and files
   with stray non-JSON lines. Anything recoverable is analyzed; skipped lines
   are reported in the run log.
3. Click **Analyze JSON**. Every inferred field appears as a tickable chip (all
   on by default), with **Select all** / **Clear all**. **Untick a field to drop
   it from the output** — it's removed from the Jolt spec and the writer schema,
   so NiFi never emits that column.
4. Click **Create JSON→CSV flow**.

The upload is used to **analyze the schema only** — it is never embedded as
sample data. The generated flow moves real files on disk:

```
GetFile → JoltTransformRecord → UpdateAttribute → PutFile
```

`JoltTransformRecord` (via a `JsonTreeReader` + `CSVRecordSetWriter`) flattens
each record, so `customer.name` becomes `customer_name`, and NDJSON works as
well as JSON arrays. `UpdateAttribute` renames the output to `.csv`.

Default directories (both auto-created):

| Role | Path | Override |
|---|---|---|
| `GetFile` pickup | `data/json-in` | `JSON_TO_CSV_INPUT_DIR` |
| `PutFile` output | `data/csv-out` | `JSON_TO_CSV_OUTPUT_DIR` |

Your upload is copied into the pickup directory so a CSV appears as soon as the
flow starts. `GetFile` consumes what it reads (normal pickup-directory
behaviour) — drop more files in any time.

> On older NiFi without `JoltTransformRecord`, the flow falls back to
> `GetFile → JoltTransformJSON → ConvertRecord → UpdateAttribute → PutFile`.
> `JoltTransformJSON` needs one whole JSON document per file, so NDJSON is not
> supported on that path.

Sample files to try: `samples/orders.json` (nested JSON array) and
`samples/metrics.ndjson` (JSON Lines).

### 7.5 Prompt assistant (chat bot)

The floating button at the bottom right. Describe a scenario in your own words
("poll an HTTP API every 5 minutes, keep only failed orders, drop them into
Kafka") and it replies with a **ready-to-run flow prompt** built from the
processors your NiFi version actually has.

- Each answer has **Use this prompt** (drops it into the Flow prompt box) and
  **Copy**.
- It reuses the API key from the top of the page but has its **own model
  dropdown** — draft with a cheap model, build with a stronger one.
- The conversation keeps context, so you can refine ("make it every 30 seconds
  and add a failure route").
- It **only writes text** — it never creates files, runs commands, or touches
  NiFi. Ask a plain question ("which Kafka processors do I have?") and you get a
  plain answer.

Detecting your NiFi version first makes suggestions concrete (it names only
installed types). If a JSON file is loaded, its columns and the GetFile/PutFile
directories are handed to the bot too.

### 7.6 Run log and flow test results

The **Run log** is color-coded by type (status, agent output, agent thinking,
tool calls, results, errors). **Export logs** downloads a timestamped `.txt`
transcript.

Once a flow deploys, **Flow test results** appear as a pass/fail table.
`flow_tests.py` interrogates the deployed group over the REST API rather than
trusting the agent's summary:

| Test case | What it proves |
|---|---|
| Process group created | The group exists on the canvas |
| Processors created | Every processor in the spec exists (including sub-groups) |
| Processor configuration valid | No processor reports a validation error |
| Processors running | Processors reached RUNNING |
| Connections wired | FlowFiles have a path through the flow |
| Controller services enabled | Readers/writers are ENABLED and valid |
| No error bulletins | No runtime ERROR/WARNING for the group |
| Data processed | At least one FlowFile actually moved |
| No stuck queue | Nothing is piling up in a queue |
| Output file written | A real file landed in the destination directory |

A row is **skipped** when it doesn't apply, and **warned** when something is
suspicious but legitimate (e.g. a source with nothing to read yet). Cases that
can't apply are never counted as passes — so a green run means the flow is
really live.

### 7.7 Token usage by model

After every flow build and every assistant reply, the token usage the Cursor SDK
reports is added to a per-model row: input, output, cache read/write, reasoning,
total, and charged cost when reported. Each row shows how the model was used
(e.g. `3× flow, 5× assistant`) and when it last ran.

Totals are written to `data/token-usage.json` (override with
`FLOW_STUDIO_USAGE_FILE`), survive restarts, and are shared across tabs.
**Refresh** re-reads them; **Reset** clears them.

---

## 8. NiFi version migration

This feature upgrades (or moves) an exported NiFi flow between versions — most
commonly a **1.x flow to 2.x**, where the breaking changes live. Enable the
**NiFi Migration** section in the UI.

### 8.1 The workflow

1. **Upload** a NiFi 1.x template (`.xml`) or a flow definition (`.json`) from
   either line.
2. The **source version is detected from the file** (or set by hand). A `.json`
   is *never* assumed to be 2.x — the version is read from the NAR bundle
   versions inside the file, and the evidence is shown next to the verdict.
3. Choose the **target version** from the supported registry.
4. Click **Analyze migration** — every component is classified against the
   target release.
5. Review the **compatibility report** (per-component verdicts + flow-level
   concerns).
6. Click **Generate migrated flow**, then **download** the migrated flow plus
   the report in Markdown and JSON.

Your uploaded file is **never modified** — migration works on a copy.

### 8.2 Supported versions

The registry (`nifi_versions.py`) currently covers:

- **1.x:** 1.16.3, 1.19.1, 1.21.0, 1.23.2, 1.25.0, 1.26.0, 1.28.1
- **2.x:** 2.0.0, 2.1.0, 2.2.0, 2.11.0

An unknown patch level (say `1.25.7`) falls back to the nearest same
`major.minor` entry rather than refusing.

### 8.3 How compatibility is decided (precedence)

The verdict is deterministic and reproducible — it does not depend on an AI
model's mood. Highest authority first:

1. **Live catalog** at the target version (if you connect one): the instance
   either has the processor type installed or it doesn't. Nothing overrides it.
2. **Curated rules** (`migration_rules.py`): known removals, renames, and
   replacements, each with an explanation.
3. **Structural checks**: e.g. `EVENT_DRIVEN` scheduling, which 2.x removed.
4. **AI model**: only annotates the components the above can't settle, and only
   with an explanation/recommendation. **It can never change a verdict** or a
   replacement type, and its text is not applied to the generated flow
   automatically. Enable it with the AI checkbox; leave it off for a purely
   deterministic run.
5. Anything still unresolved is reported honestly as **not verified** — which
   does *not* block generation; safe deterministic changes still apply.

Every row in the report names *who decided it* (catalog / rule / structural /
model), so a reviewer can tell them apart at a glance.

### 8.4 Flow-level concerns for 1.x → 2.x

The report calls out whole-flow changes no single component can tell you about,
including:

- **XML templates were removed in 2.0** — the tool converts the template into a
  2.x flow definition (JSON) so components can be recreated via *Import from
  JSON* / NiFi Registry.
- **The Variable Registry was removed in 2.0** — `${varName}` references become
  FlowFile-attribute lookups (usually empty). Move each variable into a
  Parameter Context and change the reference to `#{paramName}`. The tool reports
  the affected properties but won't rewrite them blindly.
- **NiFi 2.x requires Java 21** — confirm the target host runs Java 21 before
  deploying.

### 8.5 About the generated flow

The output is a NiFi **`VersionedFlowSnapshot`**, which NiFi deserializes
strictly — one unrecognized field and the whole import is rejected. The
generator:

- reproduces the **process-group hierarchy** rather than flattening it, so
  connections still resolve;
- emits connection endpoints as proper `ConnectableComponent` objects and checks
  every endpoint resolves to a real component before writing the file;
- imports components **disabled**, so nothing moves data before you've reviewed
  it;
- writes provenance into the root group **comments**, never as a top-level key.

Import it on the 2.x canvas via **Import from JSON** or through NiFi Registry.

---

## 9. What the agent can build

Prompts aren't limited to a flat list of processors. The agent can use NiFi's
full structural toolkit when it fits the request:

**Components**
- **Every** processor and controller service installed on the instance (359
  processors + 123 services on a stock 1.25.0), not a curated subset —
  databases, SFTP, S3, Kafka, MQTT, Elasticsearch, scripting, the full record
  family. Types are pinned to the matching NAR bundle; an uninstalled type is
  rejected rather than deployed broken.

**Canvas structure**
- Nested **sub-process groups**; **input/output ports** (including public
  site-to-site ports); **funnels**; **labels**; **remote process groups** for
  site-to-site to another NiFi. Services declared in a parent group are visible
  to nested children.

**Scheduling & runtime tuning**
- **CRON** or timer scheduling; **concurrent tasks**; **run duration**;
  `executionNode: PRIMARY` for run-once-per-cluster sources; penalty/yield
  duration; bulletin level; per-relationship **retry**.

**Queues**
- **Back pressure** thresholds; **FlowFile expiration**; **prioritizers**;
  **load balancing** for clusters; group-level defaults.

**Configuration & controller scope**
- **Parameter contexts** (`#{name}`); **reporting tasks** and
  **controller-level services** (reused by name, never duplicated on redeploy);
  process-group settings (comments, FlowFile concurrency, outbound policy).

**Rebuilding**
- `replaceExisting` stops a same-named group, disables its services, drains its
  queues, and deletes it before rebuilding — so a change updates the flow instead
  of leaving a second copy.

See `agent_prompt.py` (`SPEC_SCHEMA`) for the exact JSON shape.

---

## 10. Direct scripts (no Cursor API key)

These need only `.env` ([section 5.1](#51-env-for-the-direct-scripts-only)) and
`requests` + `python-dotenv` — no Cursor key.

### 10.1 Create the basic demo flow

```powershell
python create_basic_flow.py
```

Creates process group **`cursor-basic-demo`**:
`GenerateFlowFile` (every 5s, payload `hello-from-cursor`) → `LogAttribute`.

### 10.2 Build a flow from a hand-written spec

```powershell
python build_flow_from_spec.py flows/templates/csv_to_json.json
```

`build_flow_from_spec.py` resolves every processor/service `type` + `bundle`
against your NiFi's catalog (rejecting anything not installed), creates services,
processors, ports, funnels, labels, and nested sub-groups, wires connections
(including across group boundaries via ports), and starts everything.

### 10.3 Ask Cursor directly in chat

With `.env` working, you can skip the UI and ask in Cursor chat:

> Create a NiFi process group `demo-2` with GenerateFlowFile → UpdateAttribute → LogAttribute

The agent follows `.cursor/rules/nifi-automation.mdc` and reuses the same
helpers.

---

## 11. Writing a flow spec by hand

`build_flow_from_spec.py` consumes a JSON spec. The most common gotcha is
**property naming**.

### 11.1 Property names and values

NiFi's REST API only accepts a property's *internal* name and a value's *stored*
value, but the UI shows friendlier labels. Specs are normalized against each
component's live descriptors, so **either form works**:

| You can write | NiFi receives |
|---|---|
| `"Jolt Specification"` (display name) | `jolt-record-spec` (internal name) |
| `"Shift"` (allowable-value label) | `jolt-transform-shift` (stored value) |
| `"Schema Access Strategy"` | `schema-access-strategy` |

Keys a component doesn't declare are **dropped** (and reported as a warning), so
an invented property name can't leave the component permanently invalid.

### 11.2 User-defined properties go under `dynamicProperties`

Because unknown keys are dropped, **user-defined properties must go under
`dynamicProperties`**, not `properties` — they aren't in any descriptor:

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

This applies to anything set through user-defined entries — the attributes
`UpdateAttribute` sets, dynamic properties on controller services, and so on.

### 11.3 Structural features in a spec

The spec supports the full toolkit from [section 9](#9-what-the-agent-can-build):
nest groups under `"processGroups"`; declare `"ports"`, `"funnels"`, `"labels"`;
set `"parameterContexts"` at the top level and attach with `"parameterContext"`;
tune scheduling/retry per processor and back pressure/prioritizers per
connection; use `"replaceExisting": true` to rebuild in place. Coordinates
(`x`/`y`) are optional — omit them and an auto-layout places nodes as a
left-to-right layered graph. See `.cursor/rules/nifi-automation.mdc` for the full
field list.

---

## 12. How version awareness works

`nifi_catalog.load_catalog()` calls `/nifi-api/flow/about` to detect the running
NiFi version, then lists every installed processor and controller-service type
via `/nifi-api/flow/processor-types` and
`/nifi-api/flow/controller-service-types` — with each type's NAR bundle
(`group`/`artifact`/`version`). It's cached to `flows/.nifi-catalog.json` and
used by:

- **`build_flow_from_spec.py`** — resolves each `type` to the installed type +
  matching bundle; rejects unknown types.
- **`json_to_csv.py`** — picks the right JSON→CSV strategy for your version
  (`jolt_record` when `JoltTransformRecord` exists, then `jolt_convert_record`,
  then `convert_record` or legacy processors on older releases).
- **`agent_prompt.py`** — feeds the full catalog to the Cursor agent so it never
  guesses a class name or NAR version that doesn't exist on your instance.

This matters because processor class names and NAR bundle versions differ across
releases — a flow built for 1.24 may reference types that don't exist on 1.25,
and vice versa.

---

## 13. Running in Docker

The image runs **only Flow Studio**, not NiFi — point it at an existing NiFi
(e.g. one on your host).

```bash
chmod +x script.sh   # first time only
./script.sh          # build image + start container on :7860
./script.sh logs     # tail container logs
./script.sh stop     # stop and remove the container
```

On Windows, run it from WSL or Git Bash (`bash script.sh`), or use
`docker build` / `docker run` directly.

Open **http://localhost:7860/**. Inside the container, `127.0.0.1` is the
container itself — so when NiFi runs on your host, enter the NiFi URL as:

```
https://host.docker.internal:8443
```

`script.sh` adds the `host.docker.internal` mapping needed on Linux (Docker
Desktop on Mac/Windows provides it automatically). Generated specs are
bind-mounted to `./flows` on your host. Override the port with
`FLOW_STUDIO_PORT=8080 ./script.sh`.

> The Docker build reads `requirements.txt`. Since that file isn't in the repo
> yet (see [section 4.2](#42-install-the-python-dependencies)), create it with
> the listed packages before building the image.

---

## 14. Testing the project

The repo ships a pytest suite (version registry, migration rules, flow parsing,
the migration engine, snapshot generation, UI wiring) plus a live-server smoke
suite.

```powershell
# Unit tests (no NiFi or server needed):
python -m pytest -q

# Collect-only, to see the test inventory:
python -m pytest --collect-only
```

The `tests/smoke_migration_api.py` suite runs against a live Flow Studio server
and is the end-to-end check for the migration API.

---

## 15. Troubleshooting

**"Got an HTML page instead of a token" / login fails.**
The address points at the NiFi **UI**, not the REST API. Paste the plain server
root (`https://host:8443`); the client strips `/nifi`, `/nf`, `/nifi-api` for
you. This message also appears if you aimed at the wrong port.

**NiFi won't start / Java version error.**
`java -version` doesn't match the NiFi line. NiFi 1.x needs Java 8/11; NiFi 2.x
needs Java 21. Fix `JAVA_HOME` ([section 3.1](#31-install-a-jdk)) and restart.

**Browser certificate warning on the NiFi URL.**
Expected for a local self-signed cert — accept and continue. Flow Studio itself
disables TLS verification for these local calls.

**A flow deployed but nothing moves / processors won't start.**
Usually a **controller service stuck in ENABLING**, or an **unterminated
relationship**. The Flow test results table pinpoints which. Services must reach
ENABLED before dependent processors can run; the client waits for this, but a
misconfigured service (bad schema, missing property) will show a validation
error instead.

**JSON→CSV produced no output file.**
The `JSON_TO_CSV_*` directories are resolved by **NiFi**, not Flow Studio. If
NiFi runs in a container or WSL, it can't see this repo's `data/` folder — set
`JSON_TO_CSV_INPUT_DIR` / `JSON_TO_CSV_OUTPUT_DIR` to paths NiFi can see.

**Port 7860 already in use.**
Set `FLOW_STUDIO_PORT` to a free port before `python -m ui.app`.

**Edits to the flow-building modules seem to have no effect.**
A long-running server keeps the code it imported at startup. Set
`FLOW_STUDIO_RELOAD=1` for development, or restart the server.

**Model is blocked.**
Your Cursor team admin blocked it. The UI suggests an allowed model (e.g.
`composer-2.5` or `auto`).

**`pip install -r requirements.txt` fails — file not found.**
The file isn't in the repo yet. Use the explicit `pip install` line in
[section 4.2](#42-install-the-python-dependencies).

**Migration import into NiFi 2.x fails with "an unexpected error has occurred".**
NiFi rejects a `VersionedFlowSnapshot` wholesale on any structural deviation. Use
a freshly **generated** flow from this tool (it emits the strict format and
verifies referential integrity) rather than a hand-edited one, and confirm the
target host runs Java 21.

---

## 16. Security notes

- Local HTTPS uses NiFi's self-signed cert; the client disables TLS verify **for
  localhost only**. Do not reuse this pattern against production without proper
  trust stores.
- Cursor API keys and NiFi credentials entered in the UI are kept in the
  **browser session** (or `sessionStorage` if you check "remember") — never
  written to disk by the server.
- `.env` (used only by the direct scripts) holds NiFi secrets; keep it out of
  version control (it's git-ignored).

---

## 17. File and directory reference

| Path | Role |
|------|------|
| `ui/app.py` | Flow Studio FastAPI backend (agent runs, NiFi inspect, JSON analyze/convert, migration endpoints) |
| `ui/win_bridge_patch.py` | Fixes a Windows `select()` incompatibility in the Cursor SDK bridge |
| `ui/static/` | Flow Studio frontend (Nokia white/blue theme, collapsible sections, colorized log, export) |
| `nifi_client.py` | REST client: login/CSRF; create groups, processors, services, ports, funnels, labels, connections, parameter contexts, reporting tasks, remote groups; start/stop, drain queues, delete |
| `nifi_catalog.py` | Detect NiFi version; catalog processor/service types + NAR bundle versions; pick JSON→CSV strategy |
| `nifi_versions.py` | Registry of NiFi versions the migration feature supports, with per-version traits (templates, variable registry, event-driven, Java) |
| `json_analyze.py` | Parse JSON/NDJSON/messy captures; infer CSV columns; auto-build Jolt "Shift" specs |
| `json_to_csv.py` | Version-aware `GetFile → flatten → PutFile` JSON→CSV flow spec; owns the pickup/output dirs |
| `agent_prompt.py` | System prompt, JSON flow-spec schema, and the standing deploy-and-validate requirement |
| `prompt_assistant.py` | Chat bot that turns a scenario into a flow prompt using the live catalog |
| `usage_tracker.py` | Per-model token/cost totals; backs "Token usage by model" |
| `flow_tests.py` | Post-deployment test suite over the REST API; returns pass/fail rows |
| `flow_document.py` | Parse NiFi XML templates and 1.x/2.x JSON flow definitions; detect source version |
| `migration_engine.py` | Analyse a parsed flow against a target version; generate a migrated copy |
| `migration_rules.py` | Deterministic, hand-curated compatibility rules (removals, renames, replacements) |
| `migration_ai.py` | Narrow AI assistance for unresolved findings + the Migration Report renderer |
| `versioned_flow.py` | Build a strict `VersionedFlowSnapshot` from a 1.x XML template |
| `build_flow_from_spec.py` | Recursively build a flow from a JSON spec; normalize property names; apply tuning |
| `create_basic_flow.py` | One-shot basic demo flow |
| `build_demo_deck.py` | Builds the Nokia-templated demo PowerPoint (optional; needs `python-pptx` + `Pillow`) |
| `flow_layout.py` / `flow_plan.py` / `flow_status.py` | Auto-layout, plan/diff, and live-status helpers |
| `samples/orders.json` | Sample nested JSON array for JSON→CSV |
| `samples/metrics.ndjson` | Sample NDJSON / JSON Lines for JSON→CSV |
| `flows/` | Generated flow specs + `.nifi-catalog.json` (auto-written) |
| `flows/templates/` | Hand-written example specs |
| `migrations/` | Generated migrated flows and reports |
| `data/json-in` · `data/csv-out` | JSON→CSV pickup / output dirs (auto-created, git-ignored) |
| `data/token-usage.json` | Per-model token totals (auto-created, git-ignored) |
| `tests/` | pytest unit suite + live-server smoke suite |
| `.env` / `.env.example` | NiFi secrets for the direct scripts (never committed) |
| `.cursor/rules/nifi-automation.mdc` | How any Cursor agent should automate NiFi in this repo |
| `Dockerfile` · `script.sh` | Container image + build/run/stop/logs helper (Flow Studio only, not NiFi) |

---

*Generated as a companion to `README.md`. For the exact flow-spec JSON schema
and the full list of tunable fields, see `agent_prompt.py` and
`.cursor/rules/nifi-automation.mdc`.*
