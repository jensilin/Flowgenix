const apiKeyEl = document.getElementById("api-key");
const modelEl = document.getElementById("model");
const promptEl = document.getElementById("prompt");
const nifiUrlEl = document.getElementById("nifi-url");
const nifiUserEl = document.getElementById("nifi-user");
const nifiPassEl = document.getElementById("nifi-pass");
const rememberEl = document.getElementById("remember-nifi");
const clearNifiBtn = document.getElementById("clear-nifi");
const detectNifiBtn = document.getElementById("detect-nifi");
const nifiVersionPill = document.getElementById("nifi-version-pill");
const nifiStrategyEl = document.getElementById("nifi-strategy");
const jsonCsvToggle = document.getElementById("json-csv-toggle");
const jsonCsvBody = document.getElementById("json-csv-body");
const jsonFileEl = document.getElementById("json-file");
const jsonMetaEl = document.getElementById("json-meta");
const jsonColumnsEl = document.getElementById("json-columns");
const fieldsPanelEl = document.getElementById("json-fields");
const fieldsCountEl = document.getElementById("json-fields-count");
const fieldsAllBtn = document.getElementById("fields-all");
const fieldsNoneBtn = document.getElementById("fields-none");
const analyzeJsonBtn = document.getElementById("analyze-json");
const createJsonCsvBtn = document.getElementById("create-json-csv");
const loadModelsBtn = document.getElementById("load-models");
const form = document.getElementById("flow-form");
const createBtn = document.getElementById("create");
const downloadBtn = document.getElementById("download-json");
const downloadPanel = document.getElementById("download-panel");
const downloadMeta = document.getElementById("download-meta");
const downloadLink = document.getElementById("download-link");
const nifiLink = document.getElementById("nifi-link");
const logEl = document.getElementById("log");
const exportLogBtn = document.getElementById("export-log");
const statusPill = document.getElementById("status-pill");
const examplesEl = document.getElementById("prompt-examples");
const testPanel = document.getElementById("test-panel");
const testBodyEl = document.getElementById("test-body");
const testSummaryPill = document.getElementById("test-summary-pill");
const usageBodyEl = document.getElementById("usage-body");
const usageTotalPill = document.getElementById("usage-total-pill");
const usageRefreshBtn = document.getElementById("usage-refresh");
const usageResetBtn = document.getElementById("usage-reset");
const jsonCsvDryRunEl = document.getElementById("json-csv-dry-run");
const goldenFileEl = document.getElementById("golden-file");
const goldenMetaEl = document.getElementById("golden-meta");
const goldenClearBtn = document.getElementById("golden-clear");
const statusPanelEl = document.getElementById("status-panel");
const statusPanelPill = document.getElementById("status-summary-pill");
const statusIntervalEl = document.getElementById("status-interval");
const statusPauseBtn = document.getElementById("status-pause");
const statusStartBtn = document.getElementById("status-start");
const statusStopBtn = document.getElementById("status-stop");
const statusDeleteBtn = document.getElementById("status-delete");
const statusStripEl = document.getElementById("status-strip");
const statusProcessorsEl = document.getElementById("status-processors");
const statusConnectionsEl = document.getElementById("status-connections");
const statusBulletinListEl = document.getElementById("status-bulletin-list");
const planPanelEl = document.getElementById("plan-panel");
const planSummaryPill = document.getElementById("plan-summary-pill");
const planSummaryEl = document.getElementById("plan-summary");
const planWarningsEl = document.getElementById("plan-warnings");
const planConflictsEl = document.getElementById("plan-conflicts");
const planTreeEl = document.getElementById("plan-tree");
const planSpecEl = document.getElementById("plan-spec");
const planDeployBtn = document.getElementById("plan-deploy");
const planCancelBtn = document.getElementById("plan-cancel");
const botLauncher = document.getElementById("bot-launcher");
const botPanel = document.getElementById("bot-panel");
const botCloseBtn = document.getElementById("bot-close");
const botClearBtn = document.getElementById("bot-clear");
const botFullscreenBtn = document.getElementById("bot-fullscreen");
const botResizeHandle = document.getElementById("bot-resize-handle");
const botModelEl = document.getElementById("bot-model");
const botUsagePill = document.getElementById("bot-usage-pill");
const botMessagesEl = document.getElementById("bot-messages");
const botForm = document.getElementById("bot-form");
const botInputEl = document.getElementById("bot-input");
const botSendBtn = document.getElementById("bot-send");
const migrationToggle = document.getElementById("migration-toggle");
const migrationBody = document.getElementById("migration-body");
const migrationFileEl = document.getElementById("migration-file");
const migrationMetaEl = document.getElementById("migration-meta");
const migrationDetectEl = document.getElementById("migration-detect");
const migrationFormatPill = document.getElementById("migration-format-pill");
const migrationVersionPill = document.getElementById("migration-version-pill");
const migrationCountPill = document.getElementById("migration-count-pill");
const migrationEvidenceEl = document.getElementById("migration-evidence");
const migrationSourceEl = document.getElementById("migration-source-version");
const migrationTargetEl = document.getElementById("migration-target-version");
const migrationModelEl = document.getElementById("migration-model");
const migrationUseAiEl = document.getElementById("migration-use-ai");
const migrationAnalyzeBtn = document.getElementById("migration-analyze");
const migrationGenerateBtn = document.getElementById("migration-generate");
const migrationReportEl = document.getElementById("migration-report");
const migrationReportPill = document.getElementById("migration-report-pill");
const migrationStatsEl = document.getElementById("migration-stats");
const migrationConcernsEl = document.getElementById("migration-concerns");
const migrationDetailsEl = document.getElementById("migration-details");
const migrationWarningsEl = document.getElementById("migration-warnings");
const migrationArtifactsEl = document.getElementById("migration-artifacts");

const KEYS = {
  apiKey: "flowstudio.apiKey",
  nifiUrl: "flowstudio.nifiUrl",
  nifiUser: "flowstudio.nifiUser",
  nifiPass: "flowstudio.nifiPass",
  remember: "flowstudio.rememberNifi",
  theme: "flowstudio.theme",
  botHistory: "flowstudio.botHistory",
};

// -- Theme (light / dark) -----------------------------------------------------
// The <html> element carries data-theme="dark" whenever dark mode is active;
// styles.css keys off that attribute. Preference is persisted in localStorage.

const themeToggleBtn = document.getElementById("theme-toggle");
const themeToggleLabel = document.getElementById("theme-toggle-label");

function applyTheme(mode) {
  const dark = mode === "dark";
  document.documentElement.toggleAttribute("data-theme", false);
  if (dark) document.documentElement.setAttribute("data-theme", "dark");
  if (themeToggleLabel) themeToggleLabel.textContent = dark ? "Light" : "Dark";
  if (themeToggleBtn) themeToggleBtn.setAttribute("aria-pressed", dark ? "true" : "false");
}

function currentThemePref() {
  try {
    return localStorage.getItem(KEYS.theme) || "light";
  } catch (err) {
    return "light";
  }
}

applyTheme(currentThemePref());

if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const next = currentThemePref() === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEYS.theme, next); } catch (err) { /* ignore */ }
    applyTheme(next);
  });
}

// -- Section badges & auto-collapse ------------------------------------------
// The API-key and NiFi sections are both `<details open>` by default. Once
// they're "settled" (models loaded / version detected) we show a green badge
// on the summary and collapse the section so the prompt area gets the space.

const sectionApiEl = document.getElementById("section-api");
const sectionApiBadgeEl = document.getElementById("section-api-badge");
const sectionNifiEl = document.getElementById("section-nifi");
const sectionNifiBadgeEl = document.getElementById("section-nifi-badge");

function setSectionBadge(el, text, tone) {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("hidden", "err", "warn");
  if (tone) el.classList.add(tone);
}
function clearSectionBadge(el) {
  if (!el) return;
  el.classList.add("hidden");
  el.classList.remove("err", "warn");
}
function collapseSection(el) {
  if (el && el.tagName === "DETAILS") el.open = false;
}
function expandSection(el) {
  if (el && el.tagName === "DETAILS") el.open = true;
}

// -- Bot history persistence -------------------------------------------------
// Stored in localStorage so the assistant remembers the conversation across
// page reloads. Capped so a very long chat doesn't bloat storage or drag on
// every save.

const BOT_HISTORY_MAX = 40;

function loadBotHistory() {
  try {
    const raw = localStorage.getItem(KEYS.botHistory);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((t) => t && typeof t === "object" && typeof t.text === "string").slice(-BOT_HISTORY_MAX);
  } catch (err) {
    return [];
  }
}

function saveBotHistory(history) {
  try {
    localStorage.setItem(KEYS.botHistory, JSON.stringify(history.slice(-BOT_HISTORY_MAX)));
  } catch (err) {
    /* Storage may be full or disabled — silently ignore. */
  }
}

function clearBotHistoryPersisted() {
  try { localStorage.removeItem(KEYS.botHistory); } catch (err) { /* ignore */ }
}

let lastFlow = null;
let uploadedJson = null;
let uploadedName = "upload.json";
let nifiInfo = null;
// Set by /api/json-to-csv?dry_run=1 (and the streaming create-flow's dry-run path);
// consumed by planDeployBtn when the user approves the plan.
let pendingPlan = null;
// Golden output: text content the user has said the flow's CSV should match.
let goldenExpected = null;
let goldenName = "";
// Observability panel: which group we're watching, polling handle, cached last
// bulletin id so we can highlight only new entries between polls.
let watchGroupId = null;
let watchTimer = null;
let watchInterval = 2000;
let watchPaused = false;
let logEntries = [];
let availableColumns = [];
let selectedColumns = new Set();
let botHistory = [];
let botBusy = false;
let botSessionTokens = 0;

const COMPLEX_PROMPTS = [
  {
    title: "JSON → CSV from upload",
    text: "Create a JSON-to-CSV process group that reads and writes real files. Do NOT use GenerateFlowFile — the uploaded JSON is only for inferring the schema. Use GetFile on the input directory given in the uploaded JSON context, then JoltTransformRecord (Record Reader = JsonTreeReader, Record Writer = CSVRecordSetWriter, Shift DSL) with the auto-configured per-record spec to flatten nested fields and emit CSV, then UpdateAttribute to rename the output to .csv (set filename under dynamicProperties), then PutFile to the output directory given in that context. Auto-terminate JoltTransformRecord's original and failure relationships and both PutFile relationships, then start the group. Use only processors available on this NiFi version.",
  },
  {
    title: "CSV → JSON + failure route",
    text: "Create a process group that generates sample CSV every 15 seconds, converts it to pretty JSON with ConvertRecord (CSVReader + JsonRecordSetWriter), routes conversion failures to a LogAttribute named LogFailure (log payload), and successes to LogSuccess. Start the group. Use only processors available on this NiFi version.",
  },
  {
    title: "JSON enrich + branch",
    text: "Build a flow that generates a JSON object {\"orderId\":\"A-100\",\"amount\":120,\"region\":\"EU\"} every 10 seconds, uses EvaluateJsonPath to extract amount and region into attributes, then RouteOnAttribute: if amount > 100 go to UpdateAttribute (set priority=high) then LogAttribute; otherwise go directly to a different LogAttribute named LogLowValue. Use only processors available on this NiFi version.",
  },
  {
    title: "Split / transform / merge",
    text: "Create a process group that generates a multiline text payload with 4 lines, SplitText into one FlowFile per line, UpdateAttribute to set line.upper with an expression-language uppercased filename or uuid snippet, ReplaceText to prefix each line with 'ROW:', then MergeContent back into a single FlowFile and LogAttribute the payload. Keep it runnable with processors available on this NiFi version.",
  },
];

function setStatus(text, cls) {
  statusPill.textContent = text;
  statusPill.className = "pill" + (cls ? " " + cls : "");
}

const LOG_TAGS = {
  status: "status",
  assistant: "agent",
  thinking: "thinking",
  tool: "tool",
  event: "event",
  result: "result",
  error: "error",
  done: "done",
};

function appendLog(text, type) {
  const kind = type && LOG_TAGS[type] ? type : "status";
  const line = document.createElement("div");
  line.className = `log-line log-${kind}`;

  const dot = document.createElement("span");
  dot.className = "log-dot";
  line.appendChild(dot);

  const tag = document.createElement("span");
  tag.className = "log-tag";
  tag.textContent = LOG_TAGS[kind];
  line.appendChild(tag);

  const body = document.createElement("span");
  body.className = "log-text";
  body.textContent = text;
  line.appendChild(body);

  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;

  logEntries.push({ time: new Date(), type: kind, text: String(text) });
  exportLogBtn.disabled = logEntries.length === 0;
}

function clearLog() {
  logEl.innerHTML = "";
  logEntries = [];
  exportLogBtn.disabled = true;
  clearTestResults();
}

function exportLog() {
  if (!logEntries.length) return;
  const now = new Date();
  const header = [
    "Flow Studio run log",
    `Exported: ${now.toISOString()}`,
    `Entries: ${logEntries.length}`,
    "".padEnd(60, "-"),
  ].join("\n");
  const body = logEntries
    .map((entry) => `[${entry.time.toISOString()}] [${entry.type.toUpperCase()}] ${entry.text}`)
    .join("\n");
  const content = `${header}\n${body}\n${testReportAsText()}\n`;
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `flow-studio-log-${now.toISOString().replace(/[:.]/g, "-")}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function nifiAuth() {
  return {
    nifi_url: nifiUrlEl.value.trim(),
    nifi_username: nifiUserEl.value.trim(),
    nifi_password: nifiPassEl.value,
  };
}

function requireNifiAuth() {
  const auth = nifiAuth();
  if (!auth.nifi_url || !auth.nifi_username || !auth.nifi_password) {
    appendLog("NiFi URL, username, and password are required.", "error");
    return null;
  }
  return auth;
}

function restoreSession() {
  const savedKey = sessionStorage.getItem(KEYS.apiKey);
  if (savedKey) apiKeyEl.value = savedKey;
  const remember = sessionStorage.getItem(KEYS.remember) === "1";
  rememberEl.checked = remember;
  if (remember) {
    nifiUrlEl.value = sessionStorage.getItem(KEYS.nifiUrl) || nifiUrlEl.value;
    nifiUserEl.value = sessionStorage.getItem(KEYS.nifiUser) || "";
    nifiPassEl.value = sessionStorage.getItem(KEYS.nifiPass) || "";
  }
  updateNifiLink();
}

function persistSession() {
  sessionStorage.setItem(KEYS.apiKey, apiKeyEl.value.trim());
  if (rememberEl.checked) {
    sessionStorage.setItem(KEYS.remember, "1");
    sessionStorage.setItem(KEYS.nifiUrl, nifiUrlEl.value.trim());
    sessionStorage.setItem(KEYS.nifiUser, nifiUserEl.value.trim());
    sessionStorage.setItem(KEYS.nifiPass, nifiPassEl.value);
  } else {
    sessionStorage.removeItem(KEYS.remember);
    sessionStorage.removeItem(KEYS.nifiUrl);
    sessionStorage.removeItem(KEYS.nifiUser);
    sessionStorage.removeItem(KEYS.nifiPass);
  }
}

function clearNifiSession() {
  rememberEl.checked = false;
  sessionStorage.removeItem(KEYS.remember);
  sessionStorage.removeItem(KEYS.nifiUrl);
  sessionStorage.removeItem(KEYS.nifiUser);
  sessionStorage.removeItem(KEYS.nifiPass);
  nifiUserEl.value = "";
  nifiPassEl.value = "";
  appendLog("Cleared session-stored NiFi credentials.", "status");
  // The badge represents *this session's* detection; invalidate it too.
  clearSectionBadge(sectionNifiBadgeEl);
  expandSection(sectionNifiEl);
  nifiInfo = null;
}

function updateNifiLink() {
  const base = (nifiUrlEl.value || "https://127.0.0.1:8443").replace(/\/$/, "");
  nifiLink.href = `${base}/nifi/`;
}

function renderNifiInfo(info) {
  nifiInfo = info;
  nifiVersionPill.textContent = info.version ? `NiFi ${info.version}` : "version unknown";
  nifiVersionPill.className = "pill ok";
  const strategies = info.strategies || {};
  const parts = Object.entries(strategies)
    .filter(([key]) => key !== "anyFlow")
    .map(([key, value]) => `${key}=${value}`);
  nifiStrategyEl.textContent = (
    `NiFi ${info.version} — processors use NAR bundle ${info.processorBundleVersion || info.version}. ` +
    `Applies to all flows. ` +
    parts.join(" · ")
  );
  nifiStrategyEl.classList.remove("hidden");
}

// null = no explicit choice yet (backend keeps every inferred column).
function currentFieldSelection() {
  if (!availableColumns.length) return null;
  if (selectedColumns.size === availableColumns.length) return null;
  return availableColumns.filter((name) => selectedColumns.has(name));
}

function updateFieldCount() {
  if (!availableColumns.length) {
    fieldsPanelEl.classList.add("hidden");
    return;
  }
  fieldsPanelEl.classList.remove("hidden");
  const total = availableColumns.length;
  const picked = selectedColumns.size;
  fieldsCountEl.textContent =
    picked === total
      ? `All ${total} fields included`
      : `${picked} of ${total} fields included`;
  createJsonCsvBtn.disabled = !uploadedJson || picked === 0;
}

function setFieldSelection(names) {
  selectedColumns = new Set(names);
  for (const label of jsonColumnsEl.querySelectorAll(".chip-field")) {
    const on = selectedColumns.has(label.dataset.name);
    label.querySelector("input").checked = on;
    label.classList.toggle("off", !on);
  }
  updateFieldCount();
}

function renderColumns(columns) {
  jsonColumnsEl.innerHTML = "";
  availableColumns = (columns || []).map((c) => c.name);
  // Default to every field; the user unticks what they don't want.
  selectedColumns = new Set(availableColumns);
  for (const col of columns || []) {
    const chip = document.createElement("label");
    chip.className = "chip-field";
    chip.dataset.name = col.name;
    chip.title = `${col.name} → CSV column "${col.avroName || col.name}" (${col.kind})`;

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    box.addEventListener("change", () => {
      if (box.checked) selectedColumns.add(col.name);
      else selectedColumns.delete(col.name);
      chip.classList.toggle("off", !box.checked);
      updateFieldCount();
    });

    const text = document.createElement("span");
    text.textContent = col.name;
    const kind = document.createElement("span");
    kind.className = "chip-kind";
    kind.textContent = col.kind;

    chip.append(box, text, kind);
    jsonColumnsEl.appendChild(chip);
  }
  updateFieldCount();
}

function renderExamples() {
  examplesEl.innerHTML = "";
  for (const item of COMPLEX_PROMPTS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = item.title;
    btn.title = item.text;
    btn.addEventListener("click", () => {
      promptEl.value = item.text;
      promptEl.focus();
      appendLog(`Loaded example: ${item.title}`, "status");
    });
    examplesEl.appendChild(btn);
  }
}

function setDownload(flow) {
  lastFlow = flow;
  downloadBtn.disabled = !flow;
  if (!flow) {
    downloadPanel.classList.add("hidden");
    return;
  }
  downloadMeta.textContent = `Generated spec: ${flow.name}`;
  const blob = new Blob([flow.content], { type: "application/json" });
  const objectUrl = URL.createObjectURL(blob);
  downloadLink.href = objectUrl;
  downloadLink.download = flow.name.split("/").pop() || "flow.json";
  downloadPanel.classList.remove("hidden");
}

function fillModelSelect(select, models, preferredId) {
  const previous = select.value;
  select.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.displayName === m.id ? m.id : `${m.displayName} (${m.id})`;
    select.appendChild(opt);
  }
  const keep = models.some((m) => m.id === previous) ? previous : preferredId;
  if (keep) select.value = keep;
}

async function loadModels() {
  const apiKey = apiKeyEl.value.trim();
  if (!apiKey) {
    appendLog("Enter a Cursor API key first.", "error");
    return;
  }
  persistSession();
  setStatus("loading models", "busy");
  loadModelsBtn.disabled = true;
  try {
    const res = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to load models");
    const preferred =
      data.models.find((m) => m.id === "composer-2.5") ||
      data.models.find((m) => m.id === "auto") ||
      data.models[0];
    // The flow builder, the prompt assistant, and the migration analyser pick
    // from the same list, but each keeps its own selection.
    fillModelSelect(modelEl, data.models, preferred && preferred.id);
    fillModelSelect(botModelEl, data.models, preferred && preferred.id);
    fillModelSelect(migrationModelEl, data.models, preferred && preferred.id);
    appendLog(`Loaded ${data.models.length} models. Default: ${preferred ? preferred.id : "(none)"}`, "status");
    setStatus("ready", "ok");
    setSectionBadge(sectionApiBadgeEl, `${data.models.length} models`);
    collapseSection(sectionApiEl);
  } catch (err) {
    appendLog("Model load error: " + err.message, "error");
    setStatus("error", "err");
    setSectionBadge(sectionApiBadgeEl, "load failed", "err");
    expandSection(sectionApiEl);
  } finally {
    loadModelsBtn.disabled = false;
  }
}

async function detectNifi() {
  const auth = requireNifiAuth();
  if (!auth) return false;
  persistSession();
  detectNifiBtn.disabled = true;
  setStatus("detecting nifi", "busy");
  try {
    const res = await fetch("/api/nifi/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(auth),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "NiFi inspect failed");
    renderNifiInfo(data);
    appendLog(`Detected NiFi ${data.version} — processor NAR versions pinned to this release for every flow.`, "done");
    if (data.strategies) appendLog(`Strategies: ${JSON.stringify(data.strategies)}`, "status");
    setStatus("ready", "ok");
    setSectionBadge(sectionNifiBadgeEl, `NiFi ${data.version}`);
    collapseSection(sectionNifiEl);
    return true;
  } catch (err) {
    appendLog("NiFi detect error: " + err.message, "error");
    nifiVersionPill.textContent = "detect failed";
    nifiVersionPill.className = "pill err";
    setStatus("error", "err");
    setSectionBadge(sectionNifiBadgeEl, "detect failed", "err");
    expandSection(sectionNifiEl);
    return false;
  } finally {
    detectNifiBtn.disabled = false;
  }
}

async function readUploadedJson() {
  const file = jsonFileEl.files && jsonFileEl.files[0];
  if (!file) {
    uploadedJson = null;
    analyzeJsonBtn.disabled = true;
    createJsonCsvBtn.disabled = true;
  jsonMetaEl.textContent = "Optional input for JSON→CSV. Accepts a JSON object, a JSON array, or NDJSON / JSON Lines. The flow reads files with GetFile, auto-flattens nested fields with Jolt, and writes CSV with PutFile; processor types come from the detected NiFi version.";
    renderColumns([]);
    return;
  }
  uploadedName = file.name || "upload.json";
  uploadedJson = await file.text();
  analyzeJsonBtn.disabled = false;
  createJsonCsvBtn.disabled = false;
  // A new file invalidates any field selection made for the previous one.
  renderColumns([]);
  jsonMetaEl.textContent = `Loaded ${uploadedName} (${uploadedJson.length} chars). Analyze to infer CSV columns.`;
}

async function analyzeJson() {
  const auth = requireNifiAuth();
  if (!auth || !uploadedJson) return;
  persistSession();
  analyzeJsonBtn.disabled = true;
  setStatus("analyzing json", "busy");
  try {
    const res = await fetch("/api/json/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...auth,
        source_json: uploadedJson,
        source_filename: uploadedName,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Analyze failed");
    if (data.nifi) renderNifiInfo(data.nifi);
    const analysis = data.analysis || {};
    renderColumns(analysis.columns);
    jsonMetaEl.textContent = `${analysis.filename}: ${analysis.recordCount} records, shape=${analysis.shape}, ${ (analysis.columns || []).length } columns.`;
    appendLog(`JSON analyzed: ${jsonMetaEl.textContent}`, "done");
    (analysis.parseNotes || []).forEach((note) => appendLog(note, "status"));
    appendLog("All fields are included by default — untick any you don't want in the CSV.", "status");
    if (!promptEl.value.trim()) {
      promptEl.value = COMPLEX_PROMPTS[0].text;
    }
    setStatus("ready", "ok");
  } catch (err) {
    appendLog("JSON analyze error: " + err.message, "error");
    setStatus("error", "err");
  } finally {
    analyzeJsonBtn.disabled = !uploadedJson;
  }
}

function clearPlan() {
  pendingPlan = null;
  planPanelEl.classList.add("hidden");
  planSummaryEl.innerHTML = "";
  planWarningsEl.innerHTML = "";
  planConflictsEl.innerHTML = "";
  planTreeEl.innerHTML = "";
  planSpecEl.textContent = "";
  planSummaryPill.textContent = "plan pending";
  planSummaryPill.className = "pill";
  planDeployBtn.disabled = true;
}

function renderPlan(payload) {
  // payload = { plan, spec, specPath, ui }
  pendingPlan = payload;
  planPanelEl.classList.remove("hidden");

  const summary = (payload.plan && payload.plan.summary) || {};
  const metrics = [
    ["Process groups", summary.processGroups, ""],
    ["Processors", summary.processors, ""],
    ["Connections", summary.connections, ""],
    ["Controller services", summary.controllerServices, ""],
    ["Input ports", summary.inputPorts, ""],
    ["Output ports", summary.outputPorts, ""],
    ["Funnels", summary.funnels, ""],
    ["Remote PGs", summary.remoteProcessGroups, ""],
    ["Will replace", summary.willReplace, summary.willReplace ? "warn" : ""],
    ["Name collisions", summary.conflicts, summary.conflicts ? "err" : ""],
    ["Missing types", summary.missingTypes, summary.missingTypes ? "err" : ""],
    ["Warnings", summary.warningCount, summary.warningCount ? "warn" : ""],
  ];
  planSummaryEl.innerHTML = metrics
    .map(([label, value, cls]) => {
      const shown = value == null ? "0" : String(value);
      return `<div class="metric ${cls}"><span>${label}</span><strong>${shown}</strong></div>`;
    })
    .join("");

  const warnings = (payload.plan && payload.plan.warnings) || [];
  planWarningsEl.innerHTML = warnings.length
    ? warnings.map((w) => `<div class="item">${escapeHtml(w)}</div>`).join("")
    : `<div class="item info">The plan produced no warnings.</div>`;

  const conflicts = collectConflicts(payload.plan && payload.plan.tree);
  planConflictsEl.innerHTML = conflicts.length
    ? conflicts
        .map((c) => {
          const cls = c.action === "replace" ? "replace" : "";
          return `<div class="item ${cls}">${escapeHtml(c.text)}</div>`;
        })
        .join("")
    : "";

  planTreeEl.innerHTML = renderPlanTree(payload.plan && payload.plan.tree);
  planSpecEl.textContent = JSON.stringify(payload.spec, null, 2);

  const blocking = summary.missingTypes || summary.conflicts;
  planSummaryPill.textContent = blocking
    ? "plan has blockers"
    : summary.warningCount
    ? "plan ready (with warnings)"
    : "plan ready";
  planSummaryPill.className = blocking ? "pill err" : summary.warningCount ? "pill warn" : "pill ok";
  planDeployBtn.disabled = false;
  planDeployBtn.title = blocking
    ? "You can still deploy, but expect the deploy to fail unless you fix these first."
    : "";
}

function collectConflicts(node, acc) {
  const out = acc || [];
  if (!node) return out;
  const c = node.conflict;
  if (c) {
    const e = c.existing || {};
    const queued = e.flowFilesQueued
      ? `, ${e.flowFilesQueued} queued FlowFile(s) will be dropped`
      : "";
    if (c.action === "replace") {
      out.push({
        action: "replace",
        text: `Will replace existing group '${node.name}' (id=${c.existingId}, ${e.processors || 0} processors, ${e.connections || 0} connections${queued}).`,
      });
    } else {
      out.push({
        action: "collision",
        text: `Group '${node.name}' already exists (id=${c.existingId}) and replaceExisting is not set — a second copy with the same name will be created.`,
      });
    }
  }
  (node.processGroups || []).forEach((child) => collectConflicts(child, out));
  return out;
}

function renderPlanTree(node) {
  if (!node) return "";
  const proc = (node.processors || [])
    .map((p) => {
      const badge = p.installed
        ? '<span class="badge ok">installed</span>'
        : '<span class="badge">not installed</span>';
      return `<div class="row"><span class="name">${escapeHtml(p.name)}</span><span class="type">${escapeHtml(p.type)}</span>${badge}</div>`;
    })
    .join("");
  const svc = (node.controllerServices || [])
    .map((s) => {
      const badge = s.installed
        ? '<span class="badge ok">installed</span>'
        : '<span class="badge">not installed</span>';
      return `<div class="row"><span class="name">svc: ${escapeHtml(s.name)}</span><span class="type">${escapeHtml(s.type)}</span>${badge}</div>`;
    })
    .join("");
  const ports = (node.ports || [])
    .map((p) => `<div class="row"><span class="name">port: ${escapeHtml(p.name || "")}</span><span class="type">${escapeHtml(p.kind)}</span></div>`)
    .join("");
  const children = (node.processGroups || []).map((child) => renderPlanTree(child)).join("");
  return `<div class="group"><div class="row"><strong>${escapeHtml(node.name)}</strong>${node.replaceExisting ? '<span class="badge">replaceExisting</span>' : ""}</div>${proc}${svc}${ports}${children}</div>`;
}

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function deployPendingPlan() {
  if (!pendingPlan || !pendingPlan.specPath) {
    appendLog("Nothing to deploy — no pending plan.", "error");
    return;
  }
  const auth = nifiAuth();
  if (!auth.nifi_url) {
    appendLog("Set the NiFi URL / username / password before deploying.", "error");
    return;
  }
  planDeployBtn.disabled = true;
  setStatus("deploying", "busy");
  appendLog(`Deploying reviewed plan: ${pendingPlan.specPath}`, "status");
  try {
    const res = await fetch("/api/flows/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...auth, spec_path: pendingPlan.specPath }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    }
    appendLog(JSON.stringify(data.result, null, 2), "result");
    for (const warning of (data.result && data.result.warnings) || []) appendLog(warning, "error");
    if (data.tests) renderTestReport(data.tests);
    appendLog(`Deployed. Refresh ${data.ui || nifiLink.href}`, "done");
    setStatus(
      data.tests && data.tests.summary && !data.tests.summary.ok ? "tests failed" : "done",
      data.tests && data.tests.summary && !data.tests.summary.ok ? "err" : "ok",
    );
    const gid = data.result && data.result.processGroupId;
    if (gid) startWatching(gid);
    clearPlan();
  } catch (err) {
    appendLog("Deploy error: " + err.message, "error");
    setStatus("error", "err");
    planDeployBtn.disabled = false;
  }
}

planDeployBtn.addEventListener("click", deployPendingPlan);

async function onGoldenChange(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) {
    goldenExpected = null;
    goldenName = "";
    goldenMetaEl.textContent =
      "Drop the CSV you expect the flow to produce. After deploy, the test suite reads the file the flow wrote and reports whether it matches (line-by-line, trailing whitespace ignored).";
    goldenClearBtn.disabled = true;
    return;
  }
  try {
    goldenExpected = await file.text();
    goldenName = file.name;
    const lines = goldenExpected.split(/\r?\n/).filter((l) => l.length > 0).length;
    goldenMetaEl.textContent = `Loaded ${goldenName} (${goldenExpected.length} chars, ${lines} lines). The flow's real output will be compared against this file after deploy.`;
    goldenClearBtn.disabled = false;
  } catch (err) {
    goldenExpected = null;
    goldenName = "";
    goldenMetaEl.textContent = `Could not read ${file.name}: ${err.message}`;
    goldenClearBtn.disabled = true;
  }
}

if (goldenFileEl) {
  goldenFileEl.addEventListener("change", onGoldenChange);
}
if (goldenClearBtn) {
  goldenClearBtn.addEventListener("click", () => {
    if (goldenFileEl) goldenFileEl.value = "";
    goldenExpected = null;
    goldenName = "";
    goldenMetaEl.textContent =
      "Drop the CSV you expect the flow to produce. After deploy, the test suite reads the file the flow wrote and reports whether it matches (line-by-line, trailing whitespace ignored).";
    goldenClearBtn.disabled = true;
  });
}

// -- Observability panel -------------------------------------------------------

function startWatching(groupId) {
  if (!groupId) return;
  stopWatching();
  watchGroupId = groupId;
  watchPaused = false;
  statusPanelEl.classList.remove("hidden");
  statusPauseBtn.textContent = "Pause polling";
  statusPanelPill.textContent = "connecting…";
  statusPanelPill.className = "pill";
  pollFlowStatus();
  scheduleNextPoll();
}

function stopWatching() {
  if (watchTimer) {
    clearTimeout(watchTimer);
    watchTimer = null;
  }
  watchGroupId = null;
}

function scheduleNextPoll() {
  if (watchTimer) clearTimeout(watchTimer);
  if (!watchGroupId || watchPaused) return;
  watchTimer = setTimeout(pollFlowStatus, watchInterval);
}

async function pollFlowStatus() {
  if (!watchGroupId) return;
  const auth = nifiAuth();
  if (!auth.nifi_url) return;
  try {
    const res = await fetch("/api/flows/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...auth, group_id: watchGroupId }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      statusPanelPill.textContent = "error";
      statusPanelPill.className = "pill err";
      appendLog(
        `Status poll failed: ${
          typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)
        }`,
        "error",
      );
      return;
    }
    const snap = await res.json();
    renderFlowStatus(snap);
  } catch (err) {
    statusPanelPill.textContent = "network error";
    statusPanelPill.className = "pill err";
    appendLog("Status poll network error: " + err.message, "error");
  } finally {
    scheduleNextPoll();
  }
}

function renderFlowStatus(snap) {
  const agg = snap.aggregate || {};
  const cells = [
    ["Running", agg.running, agg.invalid ? "warn" : ""],
    ["Stopped", agg.stopped, ""],
    ["Invalid", agg.invalid, agg.invalid ? "err" : ""],
    ["Active threads", agg.activeThreadCount, ""],
    ["Queued", agg.flowFilesQueued, agg.flowFilesQueued ? "hot" : ""],
    ["In (last 5 min)", agg.input || `${agg.flowFilesIn || 0}`, ""],
    ["Out (last 5 min)", agg.output || `${agg.flowFilesOut || 0}`, ""],
    ["Read (last 5 min)", agg.read || "0 bytes", ""],
    ["Written (last 5 min)", agg.written || "0 bytes", ""],
  ];
  statusStripEl.innerHTML = cells
    .map(([label, value, cls]) => {
      const shown = value == null ? "0" : String(value);
      return `<div class="cell ${cls}"><span>${label}</span><strong>${escapeHtml(shown)}</strong></div>`;
    })
    .join("");

  const procs = snap.processors || [];
  statusProcessorsEl.innerHTML = procs.length
    ? `<table><thead><tr><th>Name</th><th>Status</th><th>In</th><th>Out</th><th>Threads</th></tr></thead><tbody>${procs
        .map((p) => {
          const cls = (p.runStatus || "").toLowerCase() === "running"
            ? "run"
            : (p.runStatus || "").toLowerCase() === "invalid"
            ? "invalid"
            : "stop";
          return `<tr><td>${escapeHtml(p.name || "")}</td><td class="${cls}">${escapeHtml(p.runStatus || "?")}</td><td>${escapeHtml(p.input || String(p.flowFilesIn || 0))}</td><td>${escapeHtml(p.output || String(p.flowFilesOut || 0))}</td><td>${p.activeThreadCount || 0}</td></tr>`;
        })
        .join("")}</tbody></table>`
    : "<p class='hint'>(no processors in this group yet)</p>";

  const conns = snap.connections || [];
  statusConnectionsEl.innerHTML = conns.length
    ? `<table><thead><tr><th>From → To</th><th>Queued</th><th>Fill</th></tr></thead><tbody>${conns
        .map((c) => {
          const fillPct = Math.max(c.percentUseCount || 0, c.percentUseBytes || 0);
          const barCls = fillPct >= 80 ? "critical" : fillPct >= 50 ? "hot" : "";
          return `<tr><td>${escapeHtml(c.sourceName || "?")} → ${escapeHtml(c.destinationName || "?")}</td><td>${escapeHtml(c.queued || `${c.flowFilesQueued || 0}`)}</td><td><div class="queue-bar ${barCls}"><span style="width:${Math.min(100, fillPct)}%"></span></div></td></tr>`;
        })
        .join("")}</tbody></table>`
    : "<p class='hint'>(no queued connections)</p>";

  const bulletins = snap.bulletins || [];
  statusBulletinListEl.innerHTML = bulletins.length
    ? bulletins
        .slice(0, 20)
        .map((b) => {
          const level = String(b.level || "INFO").toUpperCase();
          return `<div class="entry ${level}"><div class="meta">${escapeHtml(b.timestamp || "")} • ${escapeHtml(b.sourceName || "?")} • ${escapeHtml(level)}</div><div>${escapeHtml(b.message || "")}</div></div>`;
        })
        .join("")
    : "<p class='hint'>(no bulletins in the last minute)</p>";

  const runStatusRaw = String(agg.runStatus || "").toLowerCase();
  statusPanelPill.textContent = `${agg.processorCount || 0} procs • ${agg.running || 0} running • ${agg.flowFilesQueued || 0} queued`;
  statusPanelPill.className =
    agg.invalid > 0
      ? "pill err"
      : agg.stopped === agg.processorCount
      ? "pill"
      : "pill ok";
  statusStartBtn.disabled = !watchGroupId || agg.running === agg.processorCount;
  statusStopBtn.disabled = !watchGroupId || agg.stopped === agg.processorCount;
}

async function lifecycleAction(action) {
  if (!watchGroupId) return;
  const auth = nifiAuth();
  if (!auth.nifi_url) {
    appendLog("Set the NiFi URL / username / password first.", "error");
    return;
  }
  appendLog(`Requesting ${action} on group ${watchGroupId}...`, "status");
  try {
    const res = await fetch("/api/flows/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...auth, group_id: watchGroupId, action }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    appendLog(`${action} → ${JSON.stringify(data)}`, "status");
    if (action === "delete") {
      stopWatching();
      statusPanelEl.classList.add("hidden");
    } else {
      pollFlowStatus();
    }
  } catch (err) {
    appendLog(`${action} failed: ${err.message}`, "error");
  }
}

if (statusPauseBtn) {
  statusPauseBtn.addEventListener("click", () => {
    watchPaused = !watchPaused;
    statusPauseBtn.textContent = watchPaused ? "Resume polling" : "Pause polling";
    statusPanelPill.textContent = watchPaused ? "paused" : "polling";
    if (!watchPaused) scheduleNextPoll();
  });
}
if (statusIntervalEl) {
  statusIntervalEl.addEventListener("change", () => {
    watchInterval = Number(statusIntervalEl.value) || 2000;
    scheduleNextPoll();
  });
}
if (statusStartBtn) statusStartBtn.addEventListener("click", () => lifecycleAction("start"));
if (statusStopBtn) statusStopBtn.addEventListener("click", () => lifecycleAction("stop"));
if (statusDeleteBtn) {
  statusDeleteBtn.addEventListener("click", () => {
    if (!watchGroupId) return;
    if (!window.confirm(`Delete group ${watchGroupId}? This drops queued FlowFiles and cannot be undone.`)) {
      return;
    }
    lifecycleAction("delete");
  });
}


planCancelBtn.addEventListener("click", () => {
  clearPlan();
  appendLog("Plan discarded. Nothing was applied to NiFi.", "status");
  setStatus("idle", "");
});

async function createJsonToCsv() {
  const auth = requireNifiAuth();
  if (!auth || !uploadedJson) {
    appendLog("Upload a JSON file and enter NiFi credentials first.", "error");
    return;
  }
  if (availableColumns.length && selectedColumns.size === 0) {
    appendLog("Select at least one field to include in the CSV.", "error");
    return;
  }
  persistSession();
  if (!nifiInfo && !(await detectNifi())) {
    appendLog("Detect NiFi version first — it is required for every flow.", "error");
    return;
  }
  updateNifiLink();
  setDownload(null);
  clearLog();
  clearPlan();
  setStatus("running", "busy");
  createJsonCsvBtn.disabled = true;
  const dry = jsonCsvDryRunEl && jsonCsvDryRunEl.checked;
  appendLog(
    `${dry ? "Planning" : "Creating"} JSON→CSV from ${uploadedName}`,
    "status",
  );
  appendLog(`NiFi: ${auth.nifi_url}`, "status");
  try {
    const res = await fetch("/api/json-to-csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...auth,
        source_json: uploadedJson,
        source_filename: uploadedName,
        selected_columns: currentFieldSelection(),
        dry_run: jsonCsvDryRunEl && jsonCsvDryRunEl.checked,
        expected_output: goldenExpected
          ? { filename: goldenName, contents: goldenExpected }
          : null,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    if (data.nifi) renderNifiInfo(data.nifi);
    if (data.dryRun) {
      appendLog("Dry run — nothing has been created on NiFi. Review the plan below.", "status");
      if (data.spec) {
        setDownload({
          name: data.specPath || "json-to-csv.json",
          content: JSON.stringify(data.spec, null, 2),
          downloadUrl: data.downloadUrl,
        });
      }
      renderPlan({
        plan: data.plan,
        spec: data.spec,
        specPath: data.specPath,
        ui: data.ui,
      });
      setStatus("plan ready", "ok");
      return;
    }
    if (data.analysis) {
      appendLog(`CSV columns: ${(data.analysis.columns || []).join(", ")}`, "status");
      if ((data.analysis.droppedColumns || []).length) {
        appendLog(`Excluded fields: ${data.analysis.droppedColumns.join(", ")}`, "status");
      }
      (data.analysis.parseNotes || []).forEach((note) => appendLog(note, "status"));
      if (data.analysis.inputDirectory) {
        appendLog(`GetFile reads: ${data.analysis.inputDirectory}`, "status");
        appendLog(`PutFile writes CSV: ${data.analysis.outputDirectory}`, "status");
      }
      if (data.analysis.stagedInputFile) {
        appendLog(`Staged your upload for pickup: ${data.analysis.stagedInputFile}`, "status");
      }
    }
    appendLog(`NiFi ${data.nifi && data.nifi.version} strategies: ${JSON.stringify((data.nifi && data.nifi.strategies) || {})}`, "status");
    appendLog(JSON.stringify(data.result, null, 2), "result");
    for (const warning of (data.result && data.result.warnings) || []) {
      appendLog(warning, "error");
    }
    if (data.spec) {
      setDownload({
        name: data.specPath || "json-to-csv.json",
        content: JSON.stringify(data.spec, null, 2),
        downloadUrl: data.downloadUrl,
      });
    }
    appendLog(`Created process group. Refresh ${data.ui || nifiLink.href}`, "done");
    if (data.tests) renderTestReport(data.tests);
    setStatus(data.tests && data.tests.summary && !data.tests.summary.ok ? "tests failed" : "done",
              data.tests && data.tests.summary && !data.tests.summary.ok ? "err" : "ok");
    const gid = data.result && data.result.processGroupId;
    if (gid) startWatching(gid);
  } catch (err) {
    appendLog("ERROR: " + err.message, "error");
    setStatus("error", "err");
  } finally {
    createJsonCsvBtn.disabled = !uploadedJson;
  }
}

jsonCsvToggle.addEventListener("change", () => {
  const enabled = jsonCsvToggle.checked;
  jsonCsvBody.classList.toggle("hidden", !enabled);
  if (enabled) {
    appendLog("JSON → CSV upload enabled.", "status");
  } else {
    jsonFileEl.value = "";
    readUploadedJson().catch(() => {});
    appendLog("JSON → CSV upload disabled.", "status");
  }
});


exportLogBtn.addEventListener("click", exportLog);
loadModelsBtn.addEventListener("click", loadModels);
clearNifiBtn.addEventListener("click", clearNifiSession);
detectNifiBtn.addEventListener("click", detectNifi);
jsonFileEl.addEventListener("change", () => {
  readUploadedJson().catch((err) => appendLog("File read error: " + err.message, "error"));
});
analyzeJsonBtn.addEventListener("click", analyzeJson);
createJsonCsvBtn.addEventListener("click", createJsonToCsv);
fieldsAllBtn.addEventListener("click", () => setFieldSelection(availableColumns));
fieldsNoneBtn.addEventListener("click", () => setFieldSelection([]));
nifiUrlEl.addEventListener("input", updateNifiLink);
downloadBtn.addEventListener("click", () => {
  if (!lastFlow) return;
  downloadLink.click();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const apiKey = apiKeyEl.value.trim();
  const model = modelEl.value.trim();
  const prompt = promptEl.value.trim();
  const auth = requireNifiAuth();
  if (!auth) return;
  if (!apiKey || !model || !prompt) {
    appendLog("Cursor key, model, and prompt are required for prompt-based create.", "error");
    return;
  }
  persistSession();
  if (!nifiInfo && !(await detectNifi())) {
    appendLog("Detect NiFi version first — it is required for every flow.", "error");
    return;
  }
  updateNifiLink();
  setDownload(null);
  clearLog();
  setStatus("running", "busy");
  createBtn.disabled = true;
  appendLog(`Model: ${model}`, "status");
  appendLog(`NiFi: ${auth.nifi_url} (user=${auth.nifi_username})`, "status");
  appendLog(`NiFi version (all flows): ${nifiInfo.version}`, "status");
  if (nifiInfo.strategies) appendLog(`Strategies: ${JSON.stringify(nifiInfo.strategies)}`, "status");
  if (uploadedJson) appendLog(`Attached JSON: ${uploadedName}`, "status");
  appendLog(`Prompt: ${prompt}`, "status");

  try {
    const res = await fetch("/api/create-flow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        model,
        prompt,
        ...auth,
        source_json: uploadedJson,
        source_filename: uploadedName,
        selected_columns: uploadedJson ? currentFieldSelection() : null,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith("data:")) continue;
        const payload = JSON.parse(line.slice(5).trim());
        handleEvent(payload);
      }
    }
  } catch (err) {
    appendLog("ERROR: " + err.message, "error");
    setStatus("error", "err");
  } finally {
    createBtn.disabled = false;
  }
});

function handleEvent(ev) {
  switch (ev.type) {
    case "status":
      appendLog(ev.message, "status");
      break;
    case "assistant":
      appendLog(ev.text, "assistant");
      break;
    case "thinking":
      appendLog(ev.text, "thinking");
      break;
    case "tool":
      appendLog(ev.name, "tool");
      break;
    case "event":
      appendLog(ev.name, "event");
      break;
    case "nifi_info":
      if (ev.nifi) renderNifiInfo(ev.nifi);
      break;
    case "json_analysis":
      if (ev.analysis) {
        renderColumns(ev.analysis.columns);
        appendLog(`JSON columns: ${(ev.analysis.columns || []).map((c) => c.name).join(", ")}`, "status");
      }
      break;
    case "flow_json":
      setDownload({ name: ev.name, content: ev.content, downloadUrl: ev.downloadUrl });
      appendLog(`Flow JSON ready for download: ${ev.name}`, "done");
      break;
    case "tests":
      if (ev.report) {
        renderTestReport(ev.report);
        const gid = ev.report && ev.report.processGroupId;
        if (gid) startWatching(gid);
      }
      break;
    case "usage": {
      const c = ev.counts || {};
      appendLog(
        `Tokens for ${ev.model}: ${formatTokens(c.totalTokens)} total ` +
          `(in ${formatTokens(c.inputTokens)}, out ${formatTokens(c.outputTokens)}, ` +
          `cache read ${formatTokens(c.cacheReadTokens)}, write ${formatTokens(c.cacheWriteTokens)})`,
        "result"
      );
      loadUsage();
      break;
    }
    case "result":
      appendLog(`status: ${ev.status}`, ev.status === "error" ? "error" : "result");
      if (ev.text) appendLog(ev.text, ev.status === "error" ? "error" : "result");
      setStatus(ev.status === "error" ? "error" : "done", ev.status === "error" ? "err" : "ok");
      break;
    case "error":
      appendLog("ERROR: " + ev.message, "error");
      if (ev.trace) appendLog(ev.trace, "error");
      setStatus("error", "err");
      break;
    case "done":
      if (statusPill.textContent === "running") setStatus("done", "ok");
      break;
    default:
      appendLog(JSON.stringify(ev), "event");
  }
}

/* ---------- Flow test results ---------- */

let lastTestReport = null;

function clearTestResults() {
  lastTestReport = null;
  testPanel.classList.add("hidden");
  testBodyEl.innerHTML = "";
  testSummaryPill.textContent = "not run";
  testSummaryPill.className = "pill";
}

function renderTestReport(report) {
  lastTestReport = report;
  const cases = (report && report.cases) || [];
  const summary = (report && report.summary) || {};
  if (!cases.length) {
    clearTestResults();
    return;
  }

  const parts = [`${summary.passed || 0} passed`];
  if (summary.failed) parts.push(`${summary.failed} failed`);
  if (summary.warned) parts.push(`${summary.warned} warned`);
  if (summary.skipped) parts.push(`${summary.skipped} skipped`);
  testSummaryPill.textContent = parts.join(" · ");
  testSummaryPill.className = `pill ${summary.ok ? "pill-ok" : "pill-fail"}`;

  const rows = cases
    .map(
      (c) =>
        `<tr><td class="test-case">${escapeHtml(c.name)}</td>` +
        `<td><span class="test-badge test-badge-${escapeHtml(c.status)}">${escapeHtml(
          c.status
        )}</span></td>` +
        `<td class="test-desc">${escapeHtml(c.description)}</td>` +
        `<td class="test-details">${escapeHtml(c.details || "—")}</td></tr>`
    )
    .join("");

  testBodyEl.innerHTML =
    '<div class="usage-scroll"><table class="test-table">' +
    "<thead><tr><th>Test case</th><th>Result</th><th>What it checks</th><th>Detail</th></tr></thead>" +
    `<tbody>${rows}</tbody></table></div>`;
  testPanel.classList.remove("hidden");

  const label = report.processGroupName || report.processGroupId || "the new group";
  appendLog(
    `Flow tests for ${label}: ${parts.join(", ")}.`,
    summary.ok ? "done" : "error"
  );
  for (const c of cases) {
    if (c.status === "fail" || c.status === "warn") {
      appendLog(`${c.status.toUpperCase()} — ${c.name}: ${c.details}`, c.status === "fail" ? "error" : "status");
    }
  }
}

function testReportAsText() {
  if (!lastTestReport) return "";
  const s = lastTestReport.summary || {};
  const lines = [
    "",
    "=== Flow test results ===",
    `Process group: ${lastTestReport.processGroupName || ""} (${lastTestReport.processGroupId || ""})`,
    `Summary: ${s.passed || 0} passed, ${s.failed || 0} failed, ${s.warned || 0} warned, ${
      s.skipped || 0
    } skipped`,
    "",
  ];
  for (const c of lastTestReport.cases || []) {
    lines.push(`[${String(c.status).toUpperCase().padEnd(4)}] ${c.name} — ${c.description}`);
    if (c.details) lines.push(`         ${c.details}`);
  }
  return lines.join("\n");
}

/* ---------- Token usage by model ---------- */

const USAGE_COLUMNS = [
  { key: "runs", label: "Runs" },
  { key: "inputTokens", label: "Input" },
  { key: "outputTokens", label: "Output" },
  { key: "cacheReadTokens", label: "Cache read" },
  { key: "cacheWriteTokens", label: "Cache write" },
  { key: "reasoningTokens", label: "Reasoning" },
  { key: "totalTokens", label: "Total" },
];

function formatTokens(n) {
  return Number(n || 0).toLocaleString();
}

function formatCost(cents) {
  const value = Number(cents || 0);
  if (!value) return "—";
  return `$${(value / 100).toFixed(value < 100 ? 4 : 2)}`;
}

function formatWhen(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

function renderUsage(data) {
  const models = (data && data.models) || [];
  const totals = (data && data.totals) || {};
  usageTotalPill.textContent = `${formatTokens(totals.totalTokens)} tokens`;

  if (!models.length) {
    usageBodyEl.innerHTML =
      '<p class="usage-empty">No runs recorded yet. Build a flow or ask the prompt assistant and the token cost per model shows up here.</p>';
    return;
  }

  const head = USAGE_COLUMNS.map((c) => `<th>${c.label}</th>`).join("");
  const rows = models
    .map((row) => {
      const sources = Object.entries(row.sources || {})
        .map(([name, count]) => `${count}× ${name}`)
        .join(", ");
      const cells = USAGE_COLUMNS.map(
        (c) =>
          `<td class="${c.key === "totalTokens" ? "usage-total" : ""}">${
            c.key === "runs" ? formatTokens(row.runs) : formatTokens(row[c.key])
          }</td>`
      ).join("");
      return (
        `<tr><td class="usage-model">${escapeHtml(row.model)}` +
        (sources ? `<span class="usage-source">${escapeHtml(sources)}</span>` : "") +
        `</td>${cells}<td>${formatCost(row.chargedCents)}</td>` +
        `<td>${escapeHtml(formatWhen(row.lastUsed))}</td></tr>`
      );
    })
    .join("");

  const footCells = USAGE_COLUMNS.map(
    (c) => `<td>${formatTokens(totals[c.key])}</td>`
  ).join("");

  usageBodyEl.innerHTML =
    '<div class="usage-scroll"><table class="usage-table">' +
    `<thead><tr><th>Model</th>${head}<th>Charged</th><th>Last used</th></tr></thead>` +
    `<tbody>${rows}</tbody>` +
    `<tfoot><tr><td>All models</td>${footCells}<td>${formatCost(totals.chargedCents)}</td><td>—</td></tr></tfoot>` +
    "</table></div>";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

async function loadUsage() {
  try {
    const res = await fetch("/api/usage");
    if (!res.ok) throw new Error(res.statusText);
    renderUsage(await res.json());
  } catch (err) {
    usageBodyEl.innerHTML = `<p class="usage-empty">Could not load token usage: ${escapeHtml(
      err.message
    )}</p>`;
  }
}

async function resetUsage() {
  usageResetBtn.disabled = true;
  try {
    const res = await fetch("/api/usage/reset", { method: "POST" });
    if (!res.ok) throw new Error(res.statusText);
    renderUsage(await res.json());
    appendLog("Token usage totals reset.", "status");
  } catch (err) {
    appendLog("Could not reset token usage: " + err.message, "error");
  } finally {
    usageResetBtn.disabled = false;
  }
}

usageRefreshBtn.addEventListener("click", loadUsage);
usageResetBtn.addEventListener("click", resetUsage);

/* ---------- Prompt assistant chat bot ---------- */

const BOT_WELCOME =
  "Tell me what you want the flow to do — the source, what should happen to the data, and where it ends up. " +
  "I'll pick the best processors your NiFi version actually has and write the prompt for you.";

function openBot() {
  botPanel.classList.remove("hidden");
  botLauncher.setAttribute("aria-expanded", "true");
  if (!botMessagesEl.childElementCount) renderBotEmptyState();
  if (!botModelEl.value && apiKeyEl.value.trim()) loadModels();
  botInputEl.focus();
}

function closeBot() {
  botPanel.classList.add("hidden");
  botLauncher.setAttribute("aria-expanded", "false");
  botLauncher.focus();
}

function renderBotEmptyState() {
  botMessagesEl.innerHTML = "";
  const intro = document.createElement("p");
  intro.className = "bot-empty";
  intro.textContent = BOT_WELCOME;
  botMessagesEl.appendChild(intro);
}

function clearBotEmptyState() {
  const empty = botMessagesEl.querySelector(".bot-empty");
  if (empty) empty.remove();
}

function scrollBotToEnd() {
  botMessagesEl.scrollTop = botMessagesEl.scrollHeight;
}

function addBotMessage(kind, text) {
  clearBotEmptyState();
  const el = document.createElement("div");
  el.className = `bot-msg bot-msg-${kind}`;
  el.textContent = text;
  botMessagesEl.appendChild(el);
  scrollBotToEnd();
  return el;
}

// Model-name chip stamped at the top of a bot bubble. Shown as soon as the
// bubble is created (before usage counts are known) so the user always knows
// which model is answering.
function addBotModelTag(bubble, model) {
  if (!bubble || !model) return;
  if (bubble.querySelector(".bot-msg-model")) return;
  const tag = document.createElement("span");
  tag.className = "bot-msg-model";
  tag.textContent = model;
  bubble.insertBefore(tag, bubble.firstChild);
}

// Replay a saved history into the message list on page load. History items
// were stored as `{role, text}` where an assistant message may contain the
// prose reply followed by the flow prompt separated by a newline; we split
// that back out so the "Use this prompt" card still works after a reload.
function renderBotHistory(history) {
  botMessagesEl.innerHTML = "";
  for (const turn of history) {
    if (!turn || !turn.text) continue;
    if (turn.role === "user") {
      addBotMessage("user", turn.text);
      continue;
    }
    const raw = String(turn.text || "");
    const promptStart = raw.indexOf("<flow-prompt>");
    let reply = raw;
    let prompt = null;
    if (promptStart >= 0) {
      const close = raw.indexOf("</flow-prompt>", promptStart);
      reply = raw.slice(0, promptStart).trim();
      prompt = close >= 0 ? raw.slice(promptStart + 13, close).trim() : raw.slice(promptStart + 13).trim();
    } else if (raw.includes("\n")) {
      // Fallback: legacy format stored "reply\nprompt" joined by newline when
      // the assistant produced both. Only treat it as a prompt if it starts
      // with "Create" — a common opening for flow prompts — otherwise it is
      // just multi-line prose.
      const nl = raw.indexOf("\n");
      const head = raw.slice(0, nl).trim();
      const tail = raw.slice(nl + 1).trim();
      if (tail && /^create /i.test(tail)) {
        reply = head;
        prompt = tail;
      }
    }
    const bubble = addBotMessage("bot", reply || "Restored reply.");
    if (prompt) addPromptCard(bubble, prompt);
  }
  scrollBotToEnd();
}

function compactTokens(n) {
  const value = Number(n || 0);
  if (value < 1000) return String(value);
  if (value < 1000000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}k`;
  return `${(value / 1000000).toFixed(1)}M`;
}

function addBotSessionTokens(counts, model) {
  botSessionTokens += Number((counts && counts.totalTokens) || 0);
  botUsagePill.textContent = `${compactTokens(botSessionTokens)} tokens`;
  botUsagePill.title =
    `Assistant tokens this session: ${formatTokens(botSessionTokens)}` +
    (model ? ` · latest reply on ${model}` : "") +
    ". Full per-model history is in the Token usage section at the bottom of the page.";
  botUsagePill.classList.remove("hidden");
}

// Shown under the reply the tokens were spent on, so cost is visible in context.
function addBotUsageNote(bubble, counts, model) {
  if (!bubble || !counts) return;
  const note = document.createElement("span");
  note.className = "bot-msg-meta";
  note.textContent = `${formatTokens(counts.totalTokens)} tokens · ${model}`;
  note.title =
    `input ${formatTokens(counts.inputTokens)} · ` +
    `output ${formatTokens(counts.outputTokens)} · ` +
    `cache read ${formatTokens(counts.cacheReadTokens)} · ` +
    `cache write ${formatTokens(counts.cacheWriteTokens)}` +
    (counts.reasoningTokens ? ` · reasoning ${formatTokens(counts.reasoningTokens)}` : "");
  bubble.appendChild(note);
  scrollBotToEnd();
}

function addBotTyping() {
  clearBotEmptyState();
  const el = document.createElement("div");
  el.className = "bot-msg bot-msg-bot";
  el.innerHTML = '<span class="bot-typing"><span></span><span></span><span></span></span>';
  botMessagesEl.appendChild(el);
  scrollBotToEnd();
  return el;
}

function addPromptCard(container, promptText) {
  const card = document.createElement("div");
  card.className = "bot-prompt-card";

  const pre = document.createElement("pre");
  pre.textContent = promptText;

  const actions = document.createElement("div");
  actions.className = "bot-prompt-actions";

  const useBtn = document.createElement("button");
  useBtn.type = "button";
  useBtn.className = "filled";
  useBtn.textContent = "Use this prompt";
  useBtn.addEventListener("click", () => {
    promptEl.value = promptText;
    promptEl.dispatchEvent(new Event("input", { bubbles: true }));
    promptEl.scrollIntoView({ behavior: "smooth", block: "center" });
    promptEl.focus();
    appendLog("Prompt assistant filled the flow prompt.", "status");
    useBtn.textContent = "Inserted";
    setTimeout(() => (useBtn.textContent = "Use this prompt"), 1800);
  });

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "Copy";
  copyBtn.addEventListener("click", async () => {
    const ok = await copyText(promptText);
    copyBtn.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => (copyBtn.textContent = "Copy"), 1800);
  });

  actions.append(useBtn, copyBtn);
  card.append(pre, actions);
  container.appendChild(card);
  scrollBotToEnd();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API needs a secure context; fall back to a temporary selection.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

async function sendBotMessage(scenario) {
  const apiKey = apiKeyEl.value.trim();
  if (!apiKey) {
    addBotMessage("error", "Enter your Cursor API key at the top of the page first.");
    return;
  }
  if (!botModelEl.value) {
    addBotMessage("error", "Pick a model. Use “Load models” at the top to fill the list.");
    return;
  }

  persistSession();
  addBotMessage("user", scenario);
  botHistory.push({ role: "user", text: scenario });
  saveBotHistory(botHistory);

  botBusy = true;
  botSendBtn.disabled = true;
  const typing = addBotTyping();
  let statusEl = null;
  let lastUsage = null;
  let streamBubble = null;      // Bubble that fills in as tokens arrive.
  let streamCaret = null;       // Blinking caret at the tail of the stream.
  let streamText = "";          // Accumulated raw text for the current reply.
  const activeModel = botModelEl.value || "";

  try {
    const res = await fetch("/api/prompt-assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        model: botModelEl.value,
        scenario,
        history: botHistory.slice(0, -1),
        // NiFi auth is optional here; when present the bot pins its answer to
        // the installed processor catalog.
        nifi_url: nifiUrlEl.value.trim() || null,
        nifi_username: nifiUserEl.value.trim() || null,
        nifi_password: nifiPassEl.value || null,
        source_json: uploadedJson,
        source_filename: uploadedName,
        selected_columns: uploadedJson ? currentFieldSelection() : null,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answered = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5).trim());

        if (ev.type === "status") {
          if (!statusEl) {
            statusEl = document.createElement("div");
            statusEl.className = "bot-msg bot-msg-status";
            botMessagesEl.insertBefore(statusEl, typing);
          }
          statusEl.textContent = ev.message;
          scrollBotToEnd();
        } else if (ev.type === "token") {
          // Incremental text chunk. First chunk swaps the typing dots out for a
          // live bubble; subsequent chunks append to that bubble.
          const chunk = String(ev.text || "");
          if (!chunk) continue;
          if (!streamBubble) {
            typing.remove();
            streamBubble = document.createElement("div");
            streamBubble.className = "bot-msg bot-msg-bot bot-msg-streaming";
            botMessagesEl.appendChild(streamBubble);
            addBotModelTag(streamBubble, activeModel);
            const textNode = document.createElement("span");
            textNode.className = "stream-text";
            streamBubble.appendChild(textNode);
            streamCaret = document.createElement("span");
            streamCaret.className = "stream-caret";
            streamBubble.appendChild(streamCaret);
          }
          streamText += chunk;
          // Redact the prompt tag while streaming so the user sees a clean
          // prose reply; the final `reply` event replaces it with the parsed
          // reply + prompt card.
          const displayable = streamText.split("<flow-prompt>")[0];
          streamBubble.querySelector(".stream-text").textContent = displayable;
          scrollBotToEnd();
        } else if (ev.type === "usage") {
          lastUsage = { counts: ev.counts || {}, model: ev.model };
          appendLog(
            `Prompt assistant tokens for ${ev.model}: ${formatTokens(lastUsage.counts.totalTokens)} total ` +
              `(in ${formatTokens(lastUsage.counts.inputTokens)}, out ${formatTokens(lastUsage.counts.outputTokens)})`,
            "result"
          );
          addBotSessionTokens(lastUsage.counts, lastUsage.model);
          loadUsage();
        } else if (ev.type === "reply") {
          typing.remove();
          answered = true;
          let bubble;
          if (streamBubble) {
            // Reuse the streaming bubble: strip the caret, keep the model tag,
            // and swap the text for the parsed reply so the flow-prompt card
            // can attach cleanly below.
            if (streamCaret && streamCaret.parentNode) streamCaret.remove();
            const streamTextEl = streamBubble.querySelector(".stream-text");
            if (streamTextEl) streamTextEl.remove();
            streamBubble.classList.remove("bot-msg-streaming");
            const finalText = document.createTextNode(ev.text || "Here is a prompt for that scenario.");
            streamBubble.appendChild(finalText);
            bubble = streamBubble;
          } else {
            bubble = addBotMessage("bot", ev.text || "Here is a prompt for that scenario.");
            addBotModelTag(bubble, activeModel);
          }
          if (ev.prompt) {
            addPromptCard(bubble, ev.prompt);
            botHistory.push({ role: "assistant", text: `${ev.text}\n${ev.prompt}` });
          } else {
            botHistory.push({ role: "assistant", text: ev.text || "" });
          }
          saveBotHistory(botHistory);
          if (lastUsage) addBotUsageNote(bubble, lastUsage.counts, lastUsage.model);
        } else if (ev.type === "error") {
          typing.remove();
          if (streamBubble && streamBubble.parentNode) streamBubble.remove();
          answered = true;
          addBotMessage("error", ev.message);
        }
      }
    }
    if (!answered) {
      typing.remove();
      addBotMessage("error", "The assistant returned no answer. Try again or pick another model.");
    }
  } catch (err) {
    typing.remove();
    addBotMessage("error", err.message);
  } finally {
    botBusy = false;
    botSendBtn.disabled = false;
    botInputEl.focus();
  }
}

botLauncher.addEventListener("click", () => {
  if (botPanel.classList.contains("hidden")) openBot();
  else closeBot();
});
botCloseBtn.addEventListener("click", closeBot);

// Clear the conversation. Wipes the visible messages, the in-memory history,
// the persisted history, and the session-token pill so the assistant starts
// completely fresh.
if (botClearBtn) {
  botClearBtn.addEventListener("click", () => {
    if (botHistory.length === 0) return;
    if (!confirm("Clear the assistant conversation? This can't be undone.")) return;
    botHistory = [];
    clearBotHistoryPersisted();
    botMessagesEl.innerHTML = "";
    renderBotEmptyState();
    botSessionTokens = 0;
    botUsagePill.textContent = "0 tokens";
    botUsagePill.classList.add("hidden");
  });
}

// Toggle fullscreen mode.
if (botFullscreenBtn) {
  botFullscreenBtn.addEventListener("click", () => {
    botPanel.classList.toggle("fullscreen");
  });
}

// Drag the left edge to resize the panel width. `right` is fixed via CSS, so
// growing width means the left edge moves left; we snap the width to the
// distance from mouse-x to the viewport right edge minus the panel's right gap.
if (botResizeHandle) {
  let dragging = false;
  let rightGap = 24; // px, corresponds to CSS `right: 1.5rem`.
  botResizeHandle.addEventListener("pointerdown", (e) => {
    if (botPanel.classList.contains("fullscreen")) return;
    dragging = true;
    // Read the actual right gap at drag start so it stays stable during drag.
    const rect = botPanel.getBoundingClientRect();
    rightGap = Math.max(4, window.innerWidth - rect.right);
    botResizeHandle.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
  });
  botResizeHandle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const width = window.innerWidth - rightGap - e.clientX;
    const clamped = Math.min(Math.max(width, 320), window.innerWidth - 32);
    botPanel.style.width = `${clamped}px`;
  });
  const stop = (e) => {
    if (!dragging) return;
    dragging = false;
    try { botResizeHandle.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    document.body.style.userSelect = "";
  };
  botResizeHandle.addEventListener("pointerup", stop);
  botResizeHandle.addEventListener("pointercancel", stop);
}

botForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (botBusy) return;
  const scenario = botInputEl.value.trim();
  if (!scenario) return;
  botInputEl.value = "";
  sendBotMessage(scenario);
});

botInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    botForm.requestSubmit();
  }
});

// Global keyboard shortcuts. Kept small on purpose — anything more elaborate
// would need a discoverable "?" cheat-sheet.
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;

  // Esc — close bot when it is open, otherwise leave alone.
  if (e.key === "Escape" && !botPanel.classList.contains("hidden")) {
    closeBot();
    return;
  }

  // Ctrl / Cmd + Enter — submit the main prompt form. Works whether the focus
  // is inside the prompt textarea or elsewhere; the bot's own textarea handles
  // Enter internally and doesn't need this.
  if (mod && e.key === "Enter" && !botInputEl.contains(document.activeElement)) {
    if (!createBtn.disabled) {
      e.preventDefault();
      form.requestSubmit();
    }
    return;
  }

  // Ctrl / Cmd + / — toggle the prompt-assistant panel.
  if (mod && (e.key === "/" || e.key === "?")) {
    e.preventDefault();
    if (botPanel.classList.contains("hidden")) openBot();
    else closeBot();
    return;
  }

  // Ctrl / Cmd + Shift + D — toggle dark mode. Convenient for demos.
  if (mod && e.shiftKey && (e.key === "D" || e.key === "d")) {
    e.preventDefault();
    if (themeToggleBtn) themeToggleBtn.click();
    return;
  }

  // Ctrl / Cmd + Shift + F — toggle bot fullscreen (only when panel is open).
  if (mod && e.shiftKey && (e.key === "F" || e.key === "f") && !botPanel.classList.contains("hidden")) {
    e.preventDefault();
    if (botFullscreenBtn) botFullscreenBtn.click();
  }
});

// --- NiFi migration ---------------------------------------------------------

let migrationUpload = null; // { text, name }
let migrationVersions = [];
let migrationAnalysis = null;

const MIGRATION_FORMAT_LABELS = {
  xml_template: "NiFi 1.x XML template",
  json_snapshot: "NiFi flow definition (JSON)",
  studio_spec: "Flow Studio spec",
};

const MIGRATION_OUTCOME_LABELS = {
  compatible: "Compatible",
  config_change: "Config change",
  replaced: "Replaced",
  renamed: "Renamed",
  deprecated: "Deprecated",
  removed: "Removed",
  manual_review: "Manual review",
  unknown: "Not verified",
};

async function loadMigrationVersions() {
  try {
    const res = await fetch("/api/migration/versions");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to load NiFi versions");
    migrationVersions = data.versions || [];
    // Newest first, so the default target is the newest release and the default
    // source is the newest 1.x — the overwhelmingly common upgrade direction.
    fillVersionSelect(migrationTargetEl, migrationVersions, migrationVersions[0]);
    const newest1x = migrationVersions.find((v) => v.line === "1.x");
    fillVersionSelect(migrationSourceEl, migrationVersions, newest1x, true);
  } catch (err) {
    appendLog("Could not load the supported NiFi version list: " + err.message, "error");
  }
}

function fillVersionSelect(select, versions, preferred, includeAuto) {
  select.innerHTML = "";
  if (includeAuto) {
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = "Detect from file";
    select.appendChild(auto);
  }
  for (const v of versions) {
    const opt = document.createElement("option");
    opt.value = v.version;
    opt.textContent = `NiFi ${v.label}`;
    select.appendChild(opt);
  }
  if (includeAuto) select.value = "";
  else if (preferred) select.value = preferred.version;
}

async function readMigrationUpload() {
  const file = migrationFileEl.files && migrationFileEl.files[0];
  resetMigrationReport();
  if (!file) {
    migrationUpload = null;
    migrationDetectEl.classList.add("hidden");
    migrationAnalyzeBtn.disabled = true;
    migrationGenerateBtn.disabled = true;
    return;
  }

  migrationUpload = { text: await file.text(), name: file.name };
  migrationMetaEl.textContent = `Loaded ${file.name} (${formatBytes(file.size)}). Inspecting…`;

  try {
    const res = await fetch("/api/migration/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_text: migrationUpload.text,
        source_filename: migrationUpload.name,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not read the flow file");
    renderMigrationDetection(data);
    migrationAnalyzeBtn.disabled = false;
    migrationGenerateBtn.disabled = true;
  } catch (err) {
    migrationDetectEl.classList.add("hidden");
    migrationAnalyzeBtn.disabled = true;
    migrationMetaEl.textContent = `Could not read ${file.name}: ${err.message}`;
    appendLog("Migration file error: " + err.message, "error");
  }
}

function renderMigrationDetection(info) {
  migrationDetectEl.classList.remove("hidden");
  migrationFormatPill.textContent = MIGRATION_FORMAT_LABELS[info.sourceFormat] || info.sourceFormat;

  const counts = info.componentCounts || {};
  const parts = Object.keys(counts)
    .sort()
    .map((kind) => `${counts[kind]} ${kind}`);
  migrationCountPill.textContent = parts.length
    ? `${info.totalComponents} components — ${parts.join(", ")}`
    : "0 components";

  if (info.detectedVersion) {
    migrationVersionPill.textContent = `source NiFi ${info.detectedVersion}`;
    migrationVersionPill.className = "pill ok";
    // Preselect the detected version but leave it editable — detection is
    // evidence, not a decree.
    if ([...migrationSourceEl.options].some((o) => o.value === info.detectedVersion)) {
      migrationSourceEl.value = info.detectedVersion;
    }
  } else if (info.detectedLine) {
    migrationVersionPill.textContent = `source NiFi ${info.detectedLine} — pick the release`;
    migrationVersionPill.className = "pill";
    const firstOnLine = migrationVersions.find((v) => v.line === info.detectedLine);
    if (firstOnLine) migrationSourceEl.value = firstOnLine.version;
  } else {
    migrationVersionPill.textContent = "source version unknown — select it";
    migrationVersionPill.className = "pill err";
    migrationSourceEl.value = "";
  }

  const evidence = (info.detectionEvidence || []).map((e) => `• ${escapeHtml(e)}`).join("<br />");
  const types = (info.processorTypes || []).slice(0, 40).join(", ");
  migrationEvidenceEl.innerHTML =
    evidence + (types ? `<br /><br /><strong>Processor types:</strong> ${escapeHtml(types)}` : "");
  migrationMetaEl.textContent = `Loaded ${migrationUpload.name}. Your upload is not modified — migration works on a copy.`;
}

function migrationRequestBody() {
  const body = {
    source_text: migrationUpload.text,
    source_filename: migrationUpload.name,
    source_version: migrationSourceEl.value || null,
    target_version: migrationTargetEl.value,
    use_ai: migrationUseAiEl.checked,
  };
  const apiKey = apiKeyEl.value.trim();
  const model = migrationModelEl.value.trim();
  if (migrationUseAiEl.checked && apiKey && model) {
    body.api_key = apiKey;
    body.model = model;
  }
  // NiFi auth is optional here: when the connected instance runs the target
  // version its live catalog becomes the authority on what exists.
  const auth = nifiAuth();
  if (auth.nifi_url) {
    body.nifi_url = auth.nifi_url;
    body.nifi_username = auth.nifi_username;
    body.nifi_password = auth.nifi_password;
  }
  return body;
}

function resetMigrationReport() {
  migrationAnalysis = null;
  migrationReportEl.classList.add("hidden");
  migrationArtifactsEl.classList.add("hidden");
  migrationConcernsEl.classList.add("hidden");
  migrationWarningsEl.classList.add("hidden");
  migrationStatsEl.innerHTML = "";
  migrationDetailsEl.innerHTML = "";
}

async function runMigration(generate) {
  if (!migrationUpload) {
    appendLog("Upload a NiFi flow or template first.", "error");
    return;
  }
  if (!migrationTargetEl.value) {
    appendLog("Select a target NiFi version.", "error");
    return;
  }
  if (migrationUseAiEl.checked && (!apiKeyEl.value.trim() || !migrationModelEl.value.trim())) {
    appendLog(
      "AI review is on but no API key/model is set — running the deterministic analysis only.",
      "status"
    );
  }

  persistSession();
  migrationAnalyzeBtn.disabled = true;
  migrationGenerateBtn.disabled = true;
  migrationReportEl.classList.remove("hidden");
  migrationReportPill.textContent = generate ? "generating…" : "analysing…";
  migrationReportPill.className = "pill busy";
  setStatus(generate ? "generating migrated flow" : "analysing migration", "busy");

  const endpoint = generate ? "/api/migration/generate" : "/api/migration/analyze";
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(migrationRequestBody()),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }
    await consumeMigrationStream(res);
  } catch (err) {
    appendLog("Migration error: " + err.message, "error");
    migrationReportPill.textContent = "failed";
    migrationReportPill.className = "pill err";
    setStatus("error", "err");
  } finally {
    migrationAnalyzeBtn.disabled = false;
    migrationGenerateBtn.disabled = !migrationAnalysis;
  }
}

async function consumeMigrationStream(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let failed = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.trim();
      if (!line.startsWith("data:")) continue;
      let payload;
      try {
        payload = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }
      if (payload.type === "status") {
        appendLog(payload.message, "status");
      } else if (payload.type === "analysis") {
        migrationAnalysis = payload.analysis;
        renderMigrationReport(payload.analysis);
      } else if (payload.type === "generated") {
        migrationAnalysis = payload.analysis;
        renderMigrationReport(payload.analysis);
        renderMigrationArtifacts(payload.generation, payload.artifacts);
      } else if (payload.type === "usage") {
        loadUsage();
      } else if (payload.type === "error") {
        failed = payload.message;
        appendLog("Migration error: " + payload.message, "error");
      }
    }
  }

  if (failed) {
    migrationReportPill.textContent = "failed";
    migrationReportPill.className = "pill err";
    setStatus("error", "err");
    return;
  }

  const s = (migrationAnalysis && migrationAnalysis.summary) || {};
  const blocking = s.manualReview || 0;
  migrationReportPill.textContent = blocking
    ? `${blocking} need manual review`
    : "no blocking issues";
  migrationReportPill.className = "pill " + (blocking ? "err" : "ok");
  setStatus("ready", "ok");
}

function renderMigrationReport(analysis) {
  const s = analysis.summary || {};
  migrationReportEl.classList.remove("hidden");

  const stats = [
    ["Source", `NiFi ${s.sourceVersion}`, ""],
    ["Target", `NiFi ${s.targetVersion}`, ""],
    ["Components analysed", s.totalComponents, ""],
    ["Compatible", s.compatible, "ok"],
    ["Config changes", s.configChanges, s.configChanges ? "warn" : ""],
    ["Replaced", s.replaced, s.replaced ? "warn" : ""],
    ["Deprecated", s.deprecated, s.deprecated ? "warn" : ""],
    ["Removed", s.removed, s.removed ? "err" : ""],
    ["Not verified", s.unknown, s.unknown ? "warn" : ""],
    ["Manual review", s.manualReview, s.manualReview ? "err" : "ok"],
  ];
  migrationStatsEl.innerHTML = stats
    .map(
      ([label, value, tone]) =>
        `<div class="migration-stat${tone ? " tone-" + tone : ""}">
           <span class="migration-stat-value">${escapeHtml(value)}</span>
           <span class="migration-stat-label">${escapeHtml(label)}</span>
         </div>`
    )
    .join("");

  const verified = s.catalogVersion
    ? `Verified against the live NiFi ${escapeHtml(s.catalogVersion)} component catalog.`
    : "No target-version NiFi was connected, so availability came from the built-in rule base. Connect one for the most accurate result.";
  migrationStatsEl.insertAdjacentHTML("beforeend", `<p class="migration-verified">${verified}</p>`);

  const concerns = analysis.concerns || [];
  if (concerns.length) {
    migrationConcernsEl.classList.remove("hidden");
    migrationConcernsEl.innerHTML =
      `<h4>Flow-level concerns</h4>` +
      concerns
        .map(
          (c) => `<div class="migration-concern">
            <strong>${escapeHtml(c.title)}</strong>
            <p>${escapeHtml(c.explanation)}</p>
            <p class="migration-rec">${escapeHtml(c.recommendation)}</p>
          </div>`
        )
        .join("");
  } else {
    migrationConcernsEl.classList.add("hidden");
  }

  // Only components that actually need something are worth a table row;
  // listing hundreds of compatible processors buries the ones that matter.
  const interesting = (analysis.findings || []).filter((f) => f.outcome !== "compatible");
  if (interesting.length) {
    const rows = interesting
      .map((f) => {
        const changes = (f.propertyChanges || [])
          .map((c) => `${escapeHtml(c.from)} → ${escapeHtml(c.to)}`)
          .join("<br />");
        const detail = changes || escapeHtml(f.explanation || "");
        const rec = [f.recommendation, f.aiSuggestion].filter(Boolean).map(escapeHtml).join("<br />");
        return `<tr class="outcome-${escapeHtml(f.outcome)}">
          <td>${escapeHtml(f.name)}<br /><code>${escapeHtml(shortType(f.sourceType))}</code></td>
          <td><span class="outcome-tag outcome-${escapeHtml(f.outcome)}">${escapeHtml(
          MIGRATION_OUTCOME_LABELS[f.outcome] || f.outcome
        )}</span></td>
          <td>${escapeHtml(s.sourceVersion)} → ${escapeHtml(s.targetVersion)}</td>
          <td>${detail}</td>
          <td>${f.targetType ? `<code>${escapeHtml(f.targetType)}</code><br />` : ""}${rec || "—"}</td>
          <td>${f.manualReview ? '<span class="pill err">Yes</span>' : "No"}</td>
          <td><code>${escapeHtml(f.decidedBy)}</code></td>
        </tr>`;
      })
      .join("");
    migrationDetailsEl.innerHTML = `
      <h4>Components requiring attention (${interesting.length} of ${s.totalComponents})</h4>
      <div class="migration-table-wrap">
        <table class="migration-table">
          <thead><tr>
            <th>Component</th><th>Outcome</th><th>Versions</th>
            <th>Changes required</th><th>Recommended migration</th>
            <th>Manual review</th><th>Decided by</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } else {
    migrationDetailsEl.innerHTML = `<p class="migration-clean">Every component migrates to NiFi ${escapeHtml(
      s.targetVersion
    )} without changes.</p>`;
  }

  const warnings = analysis.warnings || [];
  const errors = analysis.errors || [];
  if (warnings.length || errors.length) {
    migrationWarningsEl.classList.remove("hidden");
    migrationWarningsEl.innerHTML =
      (errors.length ? `<h4>Errors</h4><ul>${errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>` : "") +
      (warnings.length
        ? `<h4>Warnings</h4><ul>${warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
        : "");
  } else {
    migrationWarningsEl.classList.add("hidden");
  }
}

function renderMigrationArtifacts(generation, artifacts) {
  if (!artifacts) return;
  migrationArtifactsEl.classList.remove("hidden");
  const g = generation || {};
  migrationArtifactsEl.innerHTML = `
    <h4>Migrated flow generated</h4>
    <p class="hint">
      ${escapeHtml(g.appliedCount || 0)} change(s) applied,
      ${escapeHtml(g.skippedCount || 0)} component(s) left unchanged for review, and
      ${escapeHtml(g.manualCount || 0)} manual-review marker(s) written into the flow.
      The original upload is untouched.
    </p>
    <div class="actions compact">
      <a class="primary" href="${escapeHtml(artifacts.migratedFlow.downloadUrl)}" download>Download migrated flow</a>
      <a class="ghost" href="${escapeHtml(artifacts.reportMarkdown.downloadUrl)}" download>Download migration report (Markdown)</a>
      <a class="ghost" href="${escapeHtml(artifacts.reportJson.downloadUrl)}" download>Download migration report (JSON)</a>
    </div>`;
  appendLog(
    `Migrated flow written to ${artifacts.migratedFlow.name} with a report alongside it.`,
    "result"
  );
}

function shortType(typeName) {
  const parts = String(typeName || "").split(".");
  return parts[parts.length - 1] || typeName || "";
}

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

migrationToggle.addEventListener("change", () => {
  const enabled = migrationToggle.checked;
  migrationBody.classList.toggle("hidden", !enabled);
  if (enabled) {
    if (!migrationVersions.length) loadMigrationVersions();
    appendLog("NiFi migration enabled.", "status");
  } else {
    migrationFileEl.value = "";
    readMigrationUpload().catch(() => {});
    appendLog("NiFi migration disabled.", "status");
  }
});

migrationFileEl.addEventListener("change", () => {
  readMigrationUpload().catch((err) =>
    appendLog("Migration file read error: " + err.message, "error")
  );
});
migrationAnalyzeBtn.addEventListener("click", () => runMigration(false));
migrationGenerateBtn.addEventListener("click", () => runMigration(true));
// Changing either version invalidates the report that was computed for the old pair.
migrationSourceEl.addEventListener("change", resetMigrationReport);
migrationTargetEl.addEventListener("change", resetMigrationReport);

restoreSession();
renderExamples();
botHistory = loadBotHistory();
if (botHistory.length) {
  renderBotHistory(botHistory);
} else {
  renderBotEmptyState();
}
loadUsage();
