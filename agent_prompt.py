"""Prompt template for Cursor SDK agents that create NiFi flows."""

from __future__ import annotations

from typing import Any

SPEC_SCHEMA = """
{
  "processGroupName": "my-flow-name",
  "position": {"x": 200, "y": 200},
  "start": true,
  "replaceExisting": false,
  "nifiVersion": "<detected-version>",
  "comments": "optional group description",
  "flowFileConcurrency": "UNBOUNDED | SINGLE_FLOWFILE_PER_NODE | SINGLE_BATCH_PER_NODE",
  "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE | BATCH_OUTPUT",
  "defaultFlowFileExpiration": "0 sec",
  "defaultBackPressureObjectThreshold": 10000,
  "defaultBackPressureDataSizeThreshold": "1 GB",
  "parameterContext": "MyContext",
  "parameterContexts": [
    {
      "name": "MyContext",
      "description": "reference these in properties as #{paramName}",
      "parameters": [{"name": "inputDir", "value": "C:/data/in", "sensitive": false}]
    }
  ],
  "controllerLevelServices": [],
  "reportingTasks": [
    {"name": "MemoryWatch", "type": "MonitorMemory", "schedulingPeriod": "5 mins",
     "properties": {}, "start": false}
  ],
  "controllerServices": [],
  "processors": [
    {
      "name": "GenerateData",
      "type": "<full-type-from-catalog>",
      "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "<nifi-version>"},
      "x": 100,
      "y": 100,
      "schedulingStrategy": "TIMER_DRIVEN | CRON_DRIVEN",
      "schedulingPeriod": "10 sec",
      "concurrentTasks": 1,
      "executionNode": "ALL | PRIMARY",
      "penaltyDuration": "30 sec",
      "yieldDuration": "1 sec",
      "bulletinLevel": "WARN",
      "runDurationMillis": 0,
      "comments": "optional processor note",
      "retryCount": 0,
      "retriedRelationships": [],
      "backoffMechanism": "PENALIZE_FLOWFILE | YIELD_PROCESSOR",
      "maxBackoffPeriod": "10 mins",
      "properties": {},
      "autoTerminated": []
    },
    {
      "name": "SetAttributes",
      "type": "<UpdateAttribute-type-from-catalog>",
      "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "<nifi-version>"},
      "x": 600,
      "y": 100,
      "properties": {},
      "dynamicProperties": {"filename": "${filename:substringBeforeLast('.')}.csv"},
      "autoTerminated": []
    },
    {
      "name": "LogResult",
      "type": "<full-type-from-catalog>",
      "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "<nifi-version>"},
      "x": 1000,
      "y": 100,
      "properties": {"Log Level": "info", "Log Payload": "true"},
      "autoTerminated": ["success"]
    }
  ],
  "funnels": [
    {"name": "MergePoint", "x": 500, "y": 400}
  ],
  "labels": [
    {"text": "Stage 1: ingest -> sub-flow transform -> log", "x": 60, "y": 20, "width": 320, "height": 70}
  ],
  "processGroups": [
    {
      "name": "SubFlow",
      "x": 500,
      "y": 100,
      "start": true,
      "controllerServices": [],
      "processors": [
        {
          "name": "TransformInSubFlow",
          "type": "<full-type-from-catalog>",
          "bundle": {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "<nifi-version>"},
          "x": 260,
          "y": 100,
          "properties": {},
          "autoTerminated": []
        }
      ],
      "ports": [
        {"name": "InputA", "kind": "input", "x": 40, "y": 100, "concurrentTasks": 1,
         "allowRemoteAccess": false, "comments": ""},
        {"name": "OutputA", "kind": "output", "x": 480, "y": 100}
      ],
      "remoteProcessGroups": [
        {"name": "PeerSite", "targetUris": "http://peer-host:8080/nifi",
         "transportProtocol": "HTTP", "x": 60, "y": 400}
      ],
      "connections": [
        {"from": "InputA", "to": "TransformInSubFlow", "relationships": ["success"]},
        {"from": "TransformInSubFlow", "to": "OutputA", "relationships": ["success"]}
      ],
      "processGroups": []
    }
  ],
  "connections": [
    {
      "from": "GenerateData",
      "to": "SubFlow.InputA",
      "relationships": ["success"],
      "name": "optional queue label",
      "backPressureObjectThreshold": 10000,
      "backPressureDataSizeThreshold": "1 GB",
      "flowFileExpiration": "0 sec",
      "prioritizers": ["FirstInFirstOutPrioritizer | OldestFlowFileFirstPrioritizer | NewestFlowFileFirstPrioritizer | PriorityAttributePrioritizer"],
      "loadBalanceStrategy": "DO_NOT_LOAD_BALANCE | ROUND_ROBIN | SINGLE_NODE | PARTITION_BY_ATTRIBUTE",
      "loadBalancePartitionAttribute": "",
      "loadBalanceCompression": "DO_NOT_COMPRESS | COMPRESS_ATTRIBUTES_ONLY | COMPRESS_ATTRIBUTES_AND_CONTENT"
    },
    {"from": "SubFlow.OutputA", "to": "LogResult", "relationships": ["success"]}
  ]
}
""".strip()


# Plain (non-f) string on purpose: this is literal guidance with no interpolation,
# so braces in the JSON examples need no escaping and cannot be misread as
# f-string placeholders.
TOOLKIT_GUIDE = """
Use NiFi's full structural toolkit, not just a flat processor list, whenever it fits the request:
- `processGroups`: nest sub-process groups inside `processGroups` (recursively — a child can have its own
  `processGroups` too). Use sub-groups to organize a complex flow into logical stages (e.g. "ingest",
  "transform", "route") or whenever the user explicitly asks for sub-process-groups / grouping.
- `ports`: each group can declare `"kind": "input"` or `"kind": "output"` ports. Ports are how flow files
  cross a process-group boundary — a parent connects directly to a child's input port and directly from a
  child's output port, using the dotted reference `"<ChildGroupName>.<PortName>"` in the PARENT's
  `connections` list (see `SubFlow.InputA` / `SubFlow.OutputA` above). Inside the child group itself, its
  own ports are referenced by their bare name (e.g. `"InputA"`, `"OutputA"`), same as processors.
- `funnels`: declare `{"name": "MergePoint", "x":.., "y":..}` in `funnels` to merge multiple connections
  into one before a downstream processor. Reference a funnel by name in `connections`, same as a processor.
- `labels`: declare `{"text": "...", "x":.., "y":.., "width":.., "height":..}` in `labels` to annotate the
  canvas (purely visual, not connectable).
- Controller services declared in a parent group are visible to processors in nested child groups too
  (no need to redeclare them) — still reference them with `@ServiceName`.
- Every group (root or nested) can set its own `"start": true|false`.
- Scheduling: set `"schedulingStrategy": "CRON_DRIVEN"` with a quartz `schedulingPeriod`
  (e.g. `"0 0 2 * * ?"` for 02:00 daily) for clock-based runs; use `concurrentTasks` for parallelism and
  `"executionNode": "PRIMARY"` for run-once-per-cluster sources. The strategy and period must be set
  together — a CRON expression with the default TIMER_DRIVEN strategy is rejected.
- Queue tuning lives on each connection: `backPressureObjectThreshold`,
  `backPressureDataSizeThreshold`, `flowFileExpiration`, `prioritizers` (short class name is fine), and
  the `loadBalance*` fields for clusters.
- Reliability per processor: `retryCount` + `retriedRelationships` + `backoffMechanism` +
  `maxBackoffPeriod` retry a relationship instead of routing failures onward; `penaltyDuration`,
  `yieldDuration`, and `bulletinLevel` tune failure behaviour and logging.
- `parameterContexts` (top level) declares reusable values; attach one to a group with
  `"parameterContext": "<name>"` and reference values in any property as `#{paramName}`. Contexts are
  reused by name if they already exist on the server.
- Group-level settings: `comments`, `flowFileConcurrency`, `flowFileOutboundPolicy`, and the
  `default*` queue fields that seed every connection in the group.
- `reportingTasks` and `controllerLevelServices` (top level) are controller-scoped; both are reused by
  name rather than duplicated on redeploy.
- `remoteProcessGroups` sets up site-to-site to another NiFi; `"allowRemoteAccess": true` on a port
  makes it a public site-to-site port.
- `"replaceExisting": true` deletes an existing group of the same name first (stopping it, disabling
  its services, and draining its queues) instead of leaving a duplicate on the canvas. Use it when the
  user asks to update, rebuild, or replace a flow they already have.
- `x` / `y` on processors / ports / funnels / labels / remote groups are OPTIONAL. If you omit them, the
  builder auto-lays out the group as a left-to-right layered flow based on the `connections` graph, which
  is usually cleaner than hand-picked coordinates. Only set `x` / `y` when the user asks for a specific
  arrangement, or add `"layout": "auto"` to a group to force a re-layout of everything in it.
""".strip()


def catalog_block(catalog_summary: dict[str, Any] | None) -> str:
    if not catalog_summary:
        return "NiFi version was not inspected. STOP and inspect first — do not guess processor types."
    version = catalog_summary.get("version") or "unknown"
    strategies = catalog_summary.get("strategies") or {}
    procs = [
        f"{item['name']} type={item['type']} bundle={item.get('bundleVersion') or '?'}"
        for item in catalog_summary.get("processors") or []
        if item.get("available") and item.get("type")
    ]
    services = [
        f"{item['name']} type={item['type']} bundle={item.get('bundleVersion') or '?'}"
        for item in catalog_summary.get("controllerServices") or []
        if item.get("available") and item.get("type")
    ]
    strategy_lines = "\n".join(f"- {key}: {value}" for key, value in strategies.items())
    all_procs = catalog_summary.get("allowedProcessorTypes") or []
    all_services = catalog_summary.get("allowedServiceTypes") or []
    proc_count = catalog_summary.get("processorCount") or len(all_procs)
    service_count = catalog_summary.get("serviceCount") or len(all_services)
    full_lists = ""
    if all_procs:
        full_lists = f"""
Every processor installed on this instance ({proc_count}) — any of these is fair game,
not just the common ones above. Writing the short name is enough; the builder resolves
the full class name and pins the NAR bundle for {version} automatically:
{", ".join(sorted(all_procs))}

Every controller service installed on this instance ({service_count}):
{", ".join(sorted(all_services))}
"""
    return f"""Detected NiFi version: {version}
Processor NAR/bundle versions on this instance should match {version}.
This applies to EVERY flow (not JSON→CSV only): prompt flows, conversions, routing, split/merge, Kafka, HTTP, etc.

Use the catalog below. Each processor has a type AND a bundle.version that belongs to this NiFi release.

Capability strategies for this version:
{strategy_lines}

Commonly used processors (name, type, NAR version):
{chr(10).join('- ' + p for p in procs) or '- (none)'}

Commonly used controller services (name, type, NAR version):
{chr(10).join('- ' + s for s in services) or '- (none)'}
{full_lists}
A machine-readable catalog is at `flows/.nifi-catalog.json`, including `allProcessors` and
`allControllerServices` with the exact type and bundle for every installed component. Read it
when you need a full class name or want to confirm something exists.

Rules for ALL cases:
- Any processor or service installed on this instance may be used — pick the one that genuinely
  fits the request rather than forcing the job onto a common processor. Reach for the specialised
  component (database, SFTP, S3, Elasticsearch, MQTT, scripting, record processors, ...) when that
  is what the user actually described.
- Do NOT use a type that is absent from the lists above; it is not installed here. If the ideal
  processor is missing, fall back to the capability strategy for this version rather than inventing
  a type.
- The short name (e.g. `TailFile`) is enough — the builder resolves the full class and pins the NAR
  bundle for this NiFi version. Give the full type only to disambiguate a name shared by two NARs.
- Different NiFi releases ship different processor class names and NAR versions — never reuse a
  type or bundle version from another version.
- The tuning fields below (scheduling strategy, retry, queue and group settings, port options) are
  checked against what NiFi {version} actually reports it supports. Anything this release does not
  understand is skipped and listed under `warnings` in the deploy output instead of failing the
  build — so ask for what the flow needs, then read the warnings and report any that were skipped.

JSON → CSV rule — read real files, write real files:
- NEVER use GenerateFlowFile for JSON→CSV. The uploaded file is used to ANALYZE the schema only;
  it is not embedded as sample data in the flow.
- The source is ALWAYS GetFile reading the input directory, and the sink is ALWAYS PutFile writing
  the output directory. Both directories are given under "Uploaded JSON context" below — use those
  exact paths. CSV conversion is the LAST transform before PutFile.
- Preferred pipeline when jsonToCsv strategy = jolt_record:
  GetFile -> JoltTransformRecord -> UpdateAttribute (rename to .csv) -> PutFile
  JoltTransformRecord uses "Record Reader" = JsonTreeReader and "Record Writer" =
  CSVRecordSetWriter, with DSL "Shift" and the per-record spec given below. Because it reads
  through a record reader, it handles NDJSON (one JSON object per line) as well as JSON arrays.
  Auto-terminate its "original" and "failure" relationships.
- If strategy = jolt_convert_record (no JoltTransformRecord on this version):
  GetFile -> JoltTransformJSON -> ConvertRecord -> UpdateAttribute -> PutFile.
  Note JoltTransformJSON needs one whole JSON document per FlowFile, so NDJSON input fails here.
- If strategy = convert_record: GetFile -> ConvertRecord -> UpdateAttribute -> PutFile.
- Use the auto-generated Jolt "Shift" spec from "Uploaded JSON context" below verbatim; it was
  built from the uploaded file's inferred columns. Only hand-write a spec if the user asks for
  logic beyond flattening.
- To name the output `.csv`, set UpdateAttribute's `filename` under "dynamicProperties" (NOT
  "properties"), e.g. {{"filename": "${{filename:substringBeforeLast('.')}}.csv"}}. Properties that
  a processor does not declare are dropped, so user-defined attributes go in dynamicProperties.
- PutFile is terminal: auto-terminate both "success" and "failure".
"""


DEPLOYMENT_MANDATE = (
    "Create, configure, connect, validate, and deploy the complete flow automatically "
    "using the NiFi REST API."
)


def needs_deployment_mandate(user_prompt: str) -> bool:
    """True when the request does not already ask for an end-to-end REST deployment."""
    text = " ".join((user_prompt or "").lower().split())
    deploys = any(word in text for word in ("deploy", "deployment"))
    via_rest = "rest api" in text or "rest-api" in text or "restapi" in text
    return not (deploys and via_rest)


def apply_deployment_mandate(user_prompt: str) -> str:
    """Append the standing deployment requirement unless the user already asked for it."""
    prompt = (user_prompt or "").strip()
    if not needs_deployment_mandate(prompt):
        return prompt
    if not prompt:
        return DEPLOYMENT_MANDATE
    if prompt[-1] not in ".!?":
        prompt += "."
    return f"{prompt}\n{DEPLOYMENT_MANDATE}"


def build_agent_prompt(
    user_prompt: str,
    nifi_url: str = "https://127.0.0.1:8443",
    catalog_summary: dict[str, Any] | None = None,
    source_json_note: str | None = None,
) -> str:
    user_prompt = apply_deployment_mandate(user_prompt)
    extra = ""
    if source_json_note:
        extra = f"\n\nUploaded JSON context:\n{source_json_note}\n"

    version = (catalog_summary or {}).get("version") or "unknown"
    return f"""You are automating Apache NiFi on this machine via this repository.

Target NiFi UI/API base: {nifi_url}
Detected NiFi version for all cases: {version}
Auth is already available to tools via process environment variables:
- NIFI_URL
- NIFI_USERNAME
- NIFI_PASSWORD
Do NOT read or write a `.env` file. Do NOT hardcode passwords in JSON or logs.

{catalog_block(catalog_summary)}

Goal: from the user's natural-language request, design and deploy a real NiFi flow using the helpers in this repo.

User request:
{user_prompt}
{extra}
Required workflow:
1. Inspect `flows/.nifi-catalog.json` (already written) and design the flow using only those types.
2. Write a NEW JSON file under `flows/` with a unique name, e.g. `flows/flow-<short-slug>.json`.
3. Follow this JSON schema. Use `@ServiceName` to reference controller services from processor properties:
{SPEC_SCHEMA}

{TOOLKIT_GUIDE}
4. Set `"nifiVersion": "{version}"` on the spec.
5. Run: `python build_flow_from_spec.py flows/<your-file>.json`
   That command pins each processor to the NAR bundle for this NiFi version and rejects types that are not installed.
6. VALIDATE the deployment before reporting success — a flow can deploy yet never run:
   - `GET /nifi-api/flow/process-groups/<new-group-id>` and check every processor's `validationErrors`
     is empty and its `state` is as intended (RUNNING when the group was started).
   - `GET /nifi-api/flow/process-groups/<new-group-id>/controller-services` and confirm each service
     is ENABLED with no `validationErrors` (a service stuck ENABLING silently stalls the whole flow).
   - `GET /nifi-api/flow/bulletin-board?groupId=<new-group-id>` and report any WARN/ERROR bulletins.
   If anything is invalid, fix the spec and re-deploy — do not report success on an invalid flow.
7. In your final answer, paste the command's JSON output, the exact flows/*.json path you wrote, the
   validation result from step 6, and tell the user to refresh {nifi_url}/nifi/

Rules:
- Build the JSON from the prompt; do not copy `flows/templates/*` unless asked.
- Use only `build_flow_from_spec.py` / `nifi_client.py` / `nifi_catalog.py`.
- Auto-terminate unused relationships.
- Keep the flow runnable. Choose sensible defaults if details are missing.
"""
