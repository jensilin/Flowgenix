const fileEl = document.getElementById("migration-file");
const dropzone = document.getElementById("dropzone");
const pasteToggle = document.getElementById("paste-toggle");
const pastePanel = document.getElementById("paste-panel");
const pasteInput = document.getElementById("paste-input");
const pasteSubmit = document.getElementById("paste-submit");
const pasteCancel = document.getElementById("paste-cancel");
const metaEl = document.getElementById("migration-meta");
const sizeAlert = document.getElementById("size-alert");
const detectEl = document.getElementById("migration-detect");
const formatPill = document.getElementById("migration-format-pill");
const versionPill = document.getElementById("migration-version-pill");
const countPill = document.getElementById("migration-count-pill");
const evidenceEl = document.getElementById("migration-evidence");
const sourceEl = document.getElementById("migration-source-version");
const targetEl = document.getElementById("migration-target-version");
const xmlRadio = document.getElementById("output-xml");
const jsonRadio = document.getElementById("output-json");
const xmlHint = document.getElementById("output-xml-hint");
const analyzeBtn = document.getElementById("migration-analyze");
const generateBtn = document.getElementById("migration-generate");
const readinessSection = document.getElementById("section-readiness");
const readinessList = document.getElementById("readiness-list");
const readinessCount = document.getElementById("readiness-count");
const logEl = document.getElementById("log");
const exportBtn = document.getElementById("export-logs");
const reportTitle = document.getElementById("migration-report-title");
const reportPill = document.getElementById("migration-report-pill");
const printMeta = document.getElementById("print-meta");
const emptyState = document.getElementById("empty-state");
const statsEl = document.getElementById("migration-stats");
const concernsEl = document.getElementById("migration-concerns");
const detailsEl = document.getElementById("migration-details");
const warningsEl = document.getElementById("migration-warnings");
const artifactsEl = document.getElementById("migration-artifacts");
const changesPanel = document.getElementById("panel-changes");
const changesFilters = document.getElementById("changes-filters");
const changesList = document.getElementById("changes-list");
const changesCount = document.getElementById("tab-count-changes");
const previewCrumbs = document.getElementById("preview-crumbs");
const previewStage = document.getElementById("preview-stage");
const previewSvg = document.getElementById("preview-svg");
const zoomInBtn = document.getElementById("preview-zoom-in");
const zoomOutBtn = document.getElementById("preview-zoom-out");
const fitBtn = document.getElementById("preview-fit");
const fidelityBody = document.getElementById("fidelity-body");
const fidelityCount = document.getElementById("tab-count-fidelity");
const checklistBody = document.getElementById("checklist-body");
const checklistProgress = document.getElementById("checklist-progress");
const checklistCount = document.getElementById("tab-count-checklist");
const printBtn = document.getElementById("print-report");
const themeToggle = document.getElementById("theme-toggle");
const themeLabel = document.getElementById("theme-toggle-label");
const workspaceEl = document.querySelector(".workspace");
const splitter = document.getElementById("pane-splitter");

const THEME_KEY = "flowgenix.theme";
const CHECKLIST_KEY = "flowgenix.checklist";
const SAMPLE_URL = "/static/samples/legacy-intake.xml";
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];
// Vercel rejects request bodies over 4.5 MB. The file travels inside a JSON
// string, which escaping makes a little larger, so stop short of the limit.
const HOSTED_BODY_LIMIT = 4.3 * 1024 * 1024;
const IS_HOSTED = !LOCAL_HOSTS.includes(location.hostname);

const FORMAT_LABELS = { xml_template: "XML template", json_snapshot: "JSON flow definition" };
const OUTCOME_LABELS = {
  compatible: "compatible",
  config_change: "config change",
  replaced: "replaced",
  renamed: "renamed",
  deprecated: "deprecated",
  removed: "removed",
  unknown: "not verified",
  manual_review: "review",
};
const OUTCOME_TONE = {
  config_change: "chg",
  replaced: "chg",
  renamed: "chg",
  deprecated: "chg",
  removed: "del",
  manual_review: "del",
};
const CHANGE_KINDS = {
  type: { label: "type replaced", tone: "chg" },
  property: { label: "property renamed", tone: "chg" },
  "property-removed": { label: "property removed", tone: "del" },
  schedulingStrategy: { label: "scheduling", tone: "chg" },
  bundleVersion: { label: "bundle version", tone: "" },
};

const state = {
  upload: null,
  analysis: null,
  readiness: null,
  generation: null,
  artifacts: null,
  fidelity: null,
  running: false,
  runId: 0,
};
let logLines = [];
let artifactUrls = [];
let migrationVersions = [];
let changeFilter = null;

// ---------- theme ----------

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme(theme, remember) {
  document.documentElement.dataset.theme = theme;
  themeLabel.textContent = theme === "dark" ? "Light" : "Dark";
  if (remember) localStorage.setItem(THEME_KEY, theme);
}

applyTheme(localStorage.getItem(THEME_KEY) || (darkQuery.matches ? "dark" : "light"), false);
darkQuery.addEventListener("change", (event) => {
  if (!localStorage.getItem(THEME_KEY)) applyTheme(event.matches ? "dark" : "light", false);
});
themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
});

// ---------- splitter ----------

// Matches --pane-min and the stacking breakpoint in styles.css.
const PANE_MIN = 300;
const stackedQuery = window.matchMedia("(max-width: 767px), (pointer: coarse) and (max-height: 540px)");
// The split is kept as a share of the width, not in pixels, so it follows the
// window. It is not remembered: every load starts at the default, which gives
// the source and settings pane three quarters of the width.
const SPLIT_DEFAULT = 0.75;
let splitRatio = SPLIT_DEFAULT;
let splitDrag = null;

function splitSpace() {
  return workspaceEl.clientWidth - splitter.offsetWidth;
}

function clampSplit(ratio) {
  const avail = splitSpace();
  if (avail < PANE_MIN * 2) return 0.5;
  return Math.min(Math.max(ratio, PANE_MIN / avail), 1 - PANE_MIN / avail);
}

function applySplit() {
  if (stackedQuery.matches) {
    workspaceEl.style.removeProperty("--left");
    return;
  }
  const avail = splitSpace();
  const left = Math.round(clampSplit(splitRatio) * avail);
  workspaceEl.style.setProperty("--left", `${left}px`);
  splitter.setAttribute("aria-valuenow", String(Math.round((left / avail) * 100)));
  splitter.setAttribute("aria-valuemin", String(Math.ceil((PANE_MIN / avail) * 100)));
  splitter.setAttribute("aria-valuemax", String(Math.floor(100 - (PANE_MIN / avail) * 100)));
}

function setSplit(ratio) {
  splitRatio = clampSplit(ratio);
  applySplit();
}

splitter.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || stackedQuery.matches) return;
  event.preventDefault();
  splitter.setPointerCapture(event.pointerId);
  splitDrag = { x: event.clientX, frame: 0 };
  splitter.classList.add("dragging");
  document.body.classList.add("is-resizing");
});

splitter.addEventListener("pointermove", (event) => {
  if (!splitDrag) return;
  splitDrag.x = event.clientX;
  if (splitDrag.frame) return;
  splitDrag.frame = requestAnimationFrame(() => {
    if (!splitDrag) return;
    splitDrag.frame = 0;
    const left = workspaceEl.getBoundingClientRect().left;
    setSplit((splitDrag.x - left - splitter.offsetWidth / 2) / splitSpace());
  });
});

function endSplitDrag() {
  if (!splitDrag) return;
  cancelAnimationFrame(splitDrag.frame);
  splitDrag = null;
  splitter.classList.remove("dragging");
  document.body.classList.remove("is-resizing");
}
splitter.addEventListener("pointerup", endSplitDrag);
splitter.addEventListener("pointercancel", endSplitDrag);
splitter.addEventListener("lostpointercapture", endSplitDrag);
splitter.addEventListener("dblclick", () => setSplit(SPLIT_DEFAULT));

splitter.addEventListener("keydown", (event) => {
  const step = event.shiftKey ? 0.1 : 0.02;
  const moves = {
    ArrowLeft: () => splitRatio - step,
    ArrowRight: () => splitRatio + step,
    Home: () => 0,
    End: () => 1,
    Enter: () => SPLIT_DEFAULT,
  };
  if (!moves[event.key]) return;
  event.preventDefault();
  setSplit(moves[event.key]());
});

new ResizeObserver(applySplit).observe(workspaceEl);
stackedQuery.addEventListener("change", applySplit);
applySplit();

// ---------- log ----------

function log(message, kind = "status") {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  logLines.push(line);
  const row = document.createElement("div");
  row.className = kind;
  row.textContent = line;
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
  exportBtn.disabled = logLines.length === 0;
}

exportBtn.addEventListener("click", () => {
  const blob = new Blob([logLines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `flowgenix-log-${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
});

// ---------- tabs ----------

const tabs = [...document.querySelectorAll(".tab")];

function selectTab(name) {
  for (const tab of tabs) {
    const active = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).classList.toggle("hidden", !active);
  }
  if (name === "preview") fitPreview();
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus();
    selectTab(next.dataset.tab);
  });
});

function setStatus(text, tone = "") {
  reportPill.textContent = text;
  reportPill.className = `status-tag${tone ? ` tone-${tone}` : ""}`;
}

// ---------- versions ----------

function fillVersionSelect(select, versions, selected) {
  select.innerHTML = "";
  const detect = document.createElement("option");
  detect.value = "";
  detect.textContent = "Detect from file";
  select.appendChild(detect);
  for (const v of versions) {
    const opt = document.createElement("option");
    opt.value = v.version;
    opt.textContent = `${v.label} (${v.line})`;
    select.appendChild(opt);
  }
  if (selected) select.value = selected;
}

async function loadMigrationVersions() {
  const resp = await fetch("/api/migration/versions");
  if (!resp.ok) throw new Error(await resp.text());
  const data = await resp.json();
  migrationVersions = data.versions || [];
  fillVersionSelect(sourceEl, migrationVersions, "");
  fillVersionSelect(targetEl, migrationVersions, migrationVersions[0]?.version || "");
  // Target select should not offer "Detect from file"
  if (targetEl.options[0]?.value === "") targetEl.remove(0);
  syncXmlAvailability();
}

function targetSupportsTemplates() {
  const chosen = migrationVersions.find((v) => v.version === targetEl.value);
  return Boolean(chosen?.supportsTemplates);
}

function syncXmlAvailability() {
  const allowed = targetSupportsTemplates();
  xmlRadio.disabled = !allowed;
  xmlHint.textContent = allowed
    ? "XML templates import on NiFi 1.x. JSON flow definitions work on 1.16+ and all 2.x."
    : "XML is disabled: NiFi 2.x removed templates. Choose JSON, or pick a 1.x target.";
  if (!allowed && xmlRadio.checked) jsonRadio.checked = true;
}

function selectedOutputFormat() {
  return xmlRadio.checked && !xmlRadio.disabled ? "xml_template" : "json_snapshot";
}

// ---------- reset ----------

function resetResults() {
  for (const url of artifactUrls) URL.revokeObjectURL(url);
  artifactUrls = [];
  state.analysis = null;
  state.readiness = null;
  state.generation = null;
  state.artifacts = null;
  state.fidelity = null;
  changeFilter = null;
  statsEl.innerHTML = "";
  statsEl.classList.add("hidden");
  concernsEl.innerHTML = "";
  concernsEl.classList.add("hidden");
  detailsEl.innerHTML = "";
  warningsEl.innerHTML = "";
  warningsEl.classList.add("hidden");
  artifactsEl.innerHTML = "";
  artifactsEl.classList.add("hidden");
  readinessSection.classList.add("hidden");
  readinessList.innerHTML = "";
  changesFilters.innerHTML = "";
  changesPanel.dataset.printEmpty = "yes";
  changesList.innerHTML = `<p class="panel-empty">Generate the migrated flow to see every change it makes.</p>`;
  changesCount.textContent = "";
  fidelityBody.innerHTML = `<p class="panel-empty">Generate the migrated flow to compare it with the source, component by component.</p>`;
  fidelityCount.textContent = "";
  resetPreview();
  renderChecklist();
  syncGenerateButton();
}

// Once a migrated file exists, the Generate button downloads it. Anything that
// would produce a different file clears the artifacts and turns it back.
function migratedFile() {
  return state.artifacts?.migratedFlow || state.artifacts?.migratedXml || null;
}

function syncGenerateButton() {
  const file = migratedFile();
  generateBtn.textContent = file ? "Download" : "Generate";
  generateBtn.title = file ? `Download ${file.name}` : "";
}

// ---------- loading a source ----------

function sizeProblem(bytes) {
  if (!IS_HOSTED || bytes <= HOSTED_BODY_LIMIT) return "";
  return (
    `This flow is ${formatBytes(bytes)}. The hosted app accepts about ${formatBytes(HOSTED_BODY_LIMIT)} ` +
    "per request. Run Flowgenix locally for larger flows (see the README); the local server has no limit."
  );
}

async function readMigrationUpload(file) {
  const tooBig = sizeProblem(file.size);
  sizeAlert.textContent = tooBig;
  sizeAlert.classList.toggle("hidden", !tooBig);
  if (tooBig) throw new Error(tooBig);

  resetResults();
  const text = await file.text();
  const resp = await fetch("/api/migration/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_text: text, source_filename: file.name }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || "Could not inspect the file");
  state.upload = { text, filename: file.name, size: file.size, info: data };
  renderMigrationDetection(data);
  analyzeBtn.disabled = false;
  generateBtn.disabled = false;
  log(`Inspected ${file.name}: ${data.sourceFormat}, ${data.totalComponents} component(s).`);
  await runMigration(false);
}

function loadText(text, name) {
  const trimmed = text.trim();
  const isXml = trimmed.startsWith("<");
  const filename = name || (isXml ? "pasted-flow.xml" : "pasted-flow.json");
  const file = new File([trimmed], filename, { type: isXml ? "application/xml" : "application/json" });
  return readMigrationUpload(file);
}

async function loadSample() {
  const resp = await fetch(SAMPLE_URL);
  if (!resp.ok) throw new Error("The sample flow could not be loaded.");
  log("Loaded the bundled sample flow (legacy-intake.xml, NiFi 1.25).");
  await loadText(await resp.text(), "legacy-intake.xml");
}

function reportError(err) {
  const message = String(err?.message || err);
  log(message, "error");
  if (!state.analysis) {
    emptyState.classList.add("hidden");
    concernsEl.classList.remove("hidden");
    concernsEl.innerHTML = notice("Could not continue", message, true);
  }
  setStatus("failed", "del");
}

function renderMigrationDetection(info) {
  detectEl.classList.remove("hidden");
  formatPill.textContent = FORMAT_LABELS[info.sourceFormat] || info.sourceFormat;
  versionPill.textContent = info.detectedVersion
    ? `NiFi ${info.detectedVersion} (${info.detectionConfidence || "detected"})`
    : `unknown${info.detectedLine ? ` · looks like ${info.detectedLine}` : ""}`;
  countPill.textContent = String(info.totalComponents || 0);
  evidenceEl.textContent = (info.detectionEvidence || []).join(" ");
  evidenceEl.classList.toggle("hidden", !evidenceEl.textContent);
  if (info.detectedVersion) sourceEl.value = info.detectedVersion;
  metaEl.textContent = `${info.filename} · ${formatBytes(state.upload?.size || 0)}`;
  emptyState.classList.add("hidden");
  reportTitle.textContent = info.filename;
  printMeta.textContent = "";
  setStatus("loaded");
}

// ---------- running ----------

function migrationRequestBody() {
  if (!state.upload) throw new Error("Load a flow file first.");
  return {
    source_text: state.upload.text,
    source_filename: state.upload.filename,
    source_version: sourceEl.value || null,
    target_version: targetEl.value,
    output_format: selectedOutputFormat(),
  };
}

async function runMigration(generate) {
  const body = migrationRequestBody();
  const runId = ++state.runId;
  resetResults();
  state.running = true;
  analyzeBtn.disabled = true;
  generateBtn.disabled = true;
  setStatus(generate ? "generating…" : "analysing…", "busy");
  log(
    `${generate ? "Generating" : "Analysing"} ${body.source_filename} for ${body.source_version || "detected"} → ${body.target_version} as ${FORMAT_LABELS[body.output_format]}.`
  );
  try {
    const path = generate ? "/api/migration/generate" : "/api/migration/analyze";
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok || !resp.body) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || err.message || `Migration request failed (HTTP ${resp.status})`);
    }
    await consumeMigrationStream(resp.body, runId);
  } finally {
    if (runId === state.runId) {
      state.running = false;
      analyzeBtn.disabled = !state.upload;
      generateBtn.disabled = !state.upload;
    }
  }
}

async function consumeMigrationStream(stream, runId) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (runId !== state.runId) {
      reader.cancel();
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.trim();
      if (line.startsWith("data:")) handleEvent(JSON.parse(line.slice(5).trim()));
    }
  }
}

function handleEvent(event) {
  if (event.type === "status") log(event.message);
  if (event.type === "error") reportError(event.message);
  if (event.type === "analysis") {
    state.analysis = event.analysis;
    state.readiness = event.readiness || null;
    renderMigrationReport(event.analysis);
    renderReadiness(state.readiness);
    renderChecklist();
  }
  if (event.type === "preview") renderPreview(event.preview);
  if (event.type === "generated") {
    state.analysis = event.analysis;
    state.generation = event.generation || {};
    state.artifacts = event.artifacts || {};
    state.fidelity = event.fidelity || null;
    renderMigrationReport(event.analysis);
    renderMigrationArtifacts(state.artifacts);
    syncGenerateButton();
    renderChanges();
    renderFidelity(state.fidelity);
    renderChecklist();
    const review = event.analysis?.summary?.manualReview;
    setStatus(review ? "generated · review needed" : "generated", review ? "chg" : "add");
    log("Migrated flow is ready to download.");
  }
}

// ---------- summary ----------

function renderMigrationReport(analysis) {
  const summary = analysis.summary || {};
  emptyState.classList.add("hidden");
  reportTitle.textContent = `NiFi ${summary.sourceVersion} → ${summary.targetVersion}`;
  printMeta.textContent = `${summary.filename || ""} · ${new Date().toLocaleString()}`;
  setStatus(summary.manualReview ? "review needed" : "ready", summary.manualReview ? "chg" : "add");

  const stats = [
    ["Components", summary.totalComponents, ""],
    ["Compatible", summary.compatible, ""],
    ["Config changes", summary.configChanges, "chg"],
    ["Removed", summary.removed, "del"],
    ["Not verified", summary.unknown, ""],
    ["Manual review", summary.manualReview, "del"],
  ];
  statsEl.classList.remove("hidden");
  statsEl.innerHTML = stats
    .map(([label, value, tone]) => {
      const cls = tone && value ? ` is-${tone}` : "";
      return `<div class="stat${cls}"><b>${value ?? 0}</b><span>${label}</span></div>`;
    })
    .join("");

  const concerns = analysis.concerns || [];
  concernsEl.classList.toggle("hidden", concerns.length === 0);
  concernsEl.innerHTML = concerns
    .map((c) => notice(c.title, [c.explanation, c.recommendation].filter(Boolean).join(" "), c.outcome === "removed"))
    .join("");

  const findings = (analysis.findings || []).filter((f) => f.outcome !== "compatible");
  if (!findings.length) {
    detailsEl.innerHTML = "<p class='panel-empty'>Every component is compatible with the target version.</p>";
  } else {
    detailsEl.innerHTML = `
      <h3 class="section-label">Components that need attention</h3>
      <table class="migration-table">
        <thead><tr><th>Component</th><th>Type</th><th>Outcome</th><th>Notes</th></tr></thead>
        <tbody>
          ${findings
            .map((f) => {
              const label = OUTCOME_LABELS[f.outcome] || f.outcome;
              // data-label carries the column heading into the stacked
              // one-block-per-finding layout phones get.
              return `<tr>
                <td data-label="Component">${escapeHtml(f.name || "")}</td>
                <td data-label="Type"><code title="${escapeHtml(f.sourceType || "")}">${escapeHtml(shortType(f.sourceType))}</code></td>
                <td data-label="Outcome"><span class="outcome-tag outcome-${escapeHtml(f.outcome)}">${escapeHtml(label)}</span></td>
                <td data-label="Notes">${escapeHtml(f.explanation || f.recommendation || "")}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
  }

  const warnings = analysis.warnings || [];
  warningsEl.classList.toggle("hidden", warnings.length === 0);
  warningsEl.innerHTML = warnings.length
    ? `<h3 class="section-label">Warnings</h3>${warnings.map((w) => notice("", w, false)).join("")}`
    : "";
}

function notice(title, body, blocking) {
  return `<div class="notice${blocking ? " is-blocking" : ""}">
    <span class="notice-glyph" aria-hidden="true">!</span>
    <div>${title ? `<strong>${escapeHtml(title)}</strong>` : ""}${body ? `<p>${escapeHtml(body)}</p>` : ""}</div>
  </div>`;
}

function renderMigrationArtifacts(artifacts) {
  const links = [];
  if (artifacts.migratedFlow) {
    links.push(artifactAnchor(artifacts.migratedFlow, "Download migrated JSON", true));
  }
  if (artifacts.migratedXml) {
    links.push(artifactAnchor(artifacts.migratedXml, "Download migrated XML", true));
  }
  if (artifacts.reportMarkdown) {
    links.push(artifactAnchor(artifacts.reportMarkdown, "Report (Markdown)"));
  }
  if (artifacts.report) {
    const name = (artifacts.reportMarkdown?.name || "migration-report.md").replace(/\.md$/, ".json");
    const text = JSON.stringify(artifacts.report, null, 2);
    links.push(
      artifactAnchor({ name, mediaType: "application/json", text, size: text.length }, "Report (JSON)")
    );
  }
  artifactsEl.classList.toggle("hidden", links.length === 0);
  artifactsEl.innerHTML = links.length ? `<h3 class="section-label">Downloads</h3>${links.join("")}` : "";
}

// The server keeps nothing on disk, so each file arrives as text and becomes a
// download here.
function artifactAnchor(item, label, primary = false) {
  const url = URL.createObjectURL(new Blob([item.text], { type: item.mediaType }));
  artifactUrls.push(url);
  const name = escapeHtml(item.name);
  const size = item.size ? ` · ${formatBytes(item.size)}` : "";
  return `<a class="download${primary ? " primary-download" : ""}" href="${url}" download="${name}">${label}<small>${name}${size}</small></a>`;
}

// ---------- import readiness ----------

function renderReadiness(readiness) {
  if (!readiness) {
    readinessSection.classList.add("hidden");
    return;
  }
  readinessSection.classList.remove("hidden");
  const items = readiness.items || [];
  const action = readiness.counts?.action || 0;
  const check = readiness.counts?.check || 0;
  readinessCount.textContent = items.length ? `${action} to fix · ${check} to check` : "";
  const rows = items.map((item) => {
    const blocking = item.severity === "action";
    const where = item.groupPath && item.groupPath !== "/" ? `${item.component} · ${item.groupPath}` : item.component;
    return `<li class="readiness-item">
      <header><span class="status-tag tone-${blocking ? "del" : "chg"}">${blocking ? "fix" : "check"}</span><strong>${escapeHtml(item.title)}</strong></header>
      <span class="readiness-where">${escapeHtml(where)}</span>
      <span class="readiness-where">${escapeHtml(item.detail)}</span>
      <p class="readiness-fix">${escapeHtml(item.fix)}</p>
    </li>`;
  });
  if (!rows.length) {
    rows.push(`<li class="readiness-item readiness-ok">Nothing found that would stop the flow starting after import.</li>`);
  }
  if (!readiness.relationshipsChecked) {
    rows.push(
      `<li class="readiness-item hint">Unconnected relationships are only checked for XML templates; JSON flow definitions do not list them.</li>`
    );
  }
  readinessList.innerHTML = rows.join("");
}

// ---------- changes ----------

function renderChanges() {
  const generation = state.generation || {};
  const changes = generation.appliedChanges || [];
  const skipped = generation.skipped || [];
  changesCount.textContent = changes.length ? String(changes.length) : "";
  changesPanel.dataset.printEmpty = changes.length || skipped.length ? "no" : "yes";

  const kinds = {};
  for (const change of changes) kinds[change.change] = (kinds[change.change] || 0) + 1;
  if (!changeFilter) {
    changeFilter = new Set(Object.keys(kinds));
    // Every component gets a bundle restamp; hide that noise while there is anything else.
    if (changeFilter.size > 1) changeFilter.delete("bundleVersion");
  }

  changesFilters.innerHTML = Object.entries(kinds)
    .map(
      ([kind, count]) =>
        `<button type="button" class="small filter-toggle" data-kind="${escapeHtml(kind)}" aria-pressed="${changeFilter.has(kind)}">${escapeHtml(CHANGE_KINDS[kind]?.label || kind)}<span class="tab-count">${count}</span></button>`
    )
    .join("");

  const groups = new Map();
  for (const change of changes) {
    if (!changeFilter.has(change.change)) continue;
    const key = change.identifier || change.component;
    if (!groups.has(key)) groups.set(key, { name: change.component, rows: [] });
    groups.get(key).rows.push(change);
  }

  const parts = [];
  if (!changes.length) {
    parts.push(`<p class="panel-empty">The migration did not need to rewrite anything.</p>`);
  } else if (!groups.size) {
    parts.push(`<p class="panel-empty">All change kinds are filtered out.</p>`);
  }
  for (const group of groups.values()) {
    parts.push(`<section class="change-group">
      <div class="change-group-head"><strong>${escapeHtml(group.name || "component")}</strong><span>${group.rows.length} change${group.rows.length === 1 ? "" : "s"}</span></div>
      ${group.rows.map(changeRow).join("")}
    </section>`);
  }
  if (skipped.length) {
    parts.push(`<h3 class="section-label">Left unchanged for manual review</h3>`);
    parts.push(`<section class="change-group">${skipped
      .map(
        (s) => `<div class="change-row">
          <span class="outcome-tag outcome-${escapeHtml(s.outcome || "manual_review")}">${escapeHtml(OUTCOME_LABELS[s.outcome] || s.outcome || "review")}</span>
          <div class="diff"><span>${escapeHtml(s.component)}</span></div>
          <p class="change-reason">${escapeHtml(s.reason || "")}</p>
        </div>`
      )
      .join("")}</section>`);
  }
  changesList.innerHTML = parts.join("");
}

function changeRow(change) {
  const kind = CHANGE_KINDS[change.change] || { label: change.change, tone: "" };
  const isType = change.change === "type";
  const from = isType ? shortType(change.from) : change.from;
  const to = isType ? shortType(change.to) : change.to;
  const diff =
    to === null || to === undefined
      ? `<span class="diff-from" title="${escapeHtml(change.from)}">${escapeHtml(from)}</span><span class="diff-arrow">removed</span>`
      : `<span class="diff-from" title="${escapeHtml(change.from)}">${escapeHtml(from ?? "—")}</span><span class="diff-arrow">→</span><span class="diff-to" title="${escapeHtml(change.to)}">${escapeHtml(to)}</span>`;
  return `<div class="change-row">
    <span class="kind-tag${kind.tone ? ` tone-${kind.tone}` : ""}">${escapeHtml(kind.label)}</span>
    <div class="diff">${diff}</div>
    ${change.reason ? `<p class="change-reason">${escapeHtml(change.reason)}</p>` : ""}
  </div>`;
}

changesFilters.addEventListener("click", (event) => {
  const button = event.target.closest(".filter-toggle");
  if (!button || !changeFilter) return;
  const kind = button.dataset.kind;
  if (changeFilter.has(kind)) changeFilter.delete(kind);
  else changeFilter.add(kind);
  renderChanges();
});

// ---------- canvas preview ----------

const preview = { data: null, group: null, view: null, bounds: null, pointers: new Map(), drag: null };

function resetPreview() {
  preview.data = null;
  preview.group = null;
  preview.view = null;
  previewCrumbs.innerHTML = "";
  previewSvg.innerHTML = `<text x="24" y="40" class="pv-sub">Analyze a flow to draw its canvas.</text>`;
  previewSvg.setAttribute("viewBox", "0 0 600 300");
}

function renderPreview(data) {
  preview.data = data;
  openGroup(data.root);
}

function openGroup(id) {
  if (!preview.data?.groups[id]) return;
  preview.group = id;
  drawGroup();
  renderCrumbs();
  fitPreview();
}

function renderCrumbs() {
  const chain = [];
  for (let id = preview.group; id; id = preview.data.groups[id]?.parent) chain.unshift(preview.data.groups[id]);
  previewCrumbs.innerHTML = chain
    .map((g, i) => {
      const current = i === chain.length - 1;
      const sep = i ? `<span class="crumb-sep" aria-hidden="true">/</span>` : "";
      return `${sep}<button type="button" class="crumb" data-group="${escapeHtml(g.id)}"${current ? ' aria-current="page"' : ""}>${escapeHtml(g.name)}</button>`;
    })
    .join("");
}

previewCrumbs.addEventListener("click", (event) => {
  const crumb = event.target.closest(".crumb");
  if (crumb && crumb.getAttribute("aria-current") !== "page") openGroup(crumb.dataset.group);
});

function drawGroup() {
  const { groups, sizes } = preview.data;
  const group = groups[preview.group];
  const flagged = new Set(
    (state.readiness?.items || []).filter((i) => i.severity === "action").map((i) => i.componentId)
  );
  const boxes = new Map();
  const shapes = [];
  const add = (id, x, y, w, h) => boxes.set(id, { x, y, w, h });

  for (const label of group.labels) {
    add(label.id, label.x, label.y, label.w, label.h);
    const first = (label.text || "").split("\n")[0];
    shapes.push(`<g class="pv-label" transform="translate(${label.x} ${label.y})">
      <rect width="${label.w}" height="${label.h}" rx="2"/>
      <text x="10" y="22">${escapeHtml(truncate(first, Math.max(8, Math.floor(label.w / 8))))}</text></g>`);
  }
  for (const child of group.children) {
    const [w, h] = sizes.group;
    add(child.id, child.x, child.y, w, h);
    const tone = OUTCOME_TONE[child.outcome] ? ` pv-${OUTCOME_TONE[child.outcome]}` : "";
    shapes.push(`<g class="pv-group${tone}" data-group="${escapeHtml(child.id)}" transform="translate(${child.x} ${child.y})" tabindex="0" role="button" aria-label="Open ${escapeHtml(child.name)}">
      <title>Open ${escapeHtml(child.name)}</title>
      <rect class="pv-box" width="${w}" height="${h}" rx="2"/>
      <text class="pv-name" x="20" y="40">${escapeHtml(truncate(child.name, 34))}</text>
      <text class="pv-sub" x="20" y="66">${child.processorCount} processor${child.processorCount === 1 ? "" : "s"}</text>
      <text class="pv-sub" x="20" y="${h - 22}">process group · open ›</text></g>`);
  }
  for (const proc of group.processors) {
    const [w, h] = sizes.processor;
    add(proc.id, proc.x, proc.y, w, h);
    const toneKey = OUTCOME_TONE[proc.outcome];
    const tone = toneKey ? ` pv-${toneKey}` : "";
    const tag = toneKey ? `<text class="pv-tagtext" x="20" y="${h - 20}">${escapeHtml(OUTCOME_LABELS[proc.outcome] || proc.outcome)}</text>` : "";
    const flag = flagged.has(proc.id)
      ? `<g class="pv-warn"><rect class="pv-flag" x="${w - 26}" y="12" width="14" height="14" rx="2"/><title>Import readiness: needs a fix</title></g>`
      : "";
    shapes.push(`<g class="pv-proc${tone}" transform="translate(${proc.x} ${proc.y})">
      <title>${escapeHtml(`${proc.name} (${proc.type})`)}</title>
      <rect class="pv-box" width="${w}" height="${h}" rx="2"/>
      <text class="pv-name" x="20" y="38">${escapeHtml(truncate(proc.name, 34))}</text>
      <text class="pv-sub" x="20" y="62">${escapeHtml(truncate(proc.type, 40))}</text>
      ${tag}${flag}</g>`);
  }
  for (const port of group.ports) {
    const [w, h] = sizes.port;
    add(port.id, port.x, port.y, w, h);
    shapes.push(`<g class="pv-port" transform="translate(${port.x} ${port.y})">
      <rect class="pv-box" width="${w}" height="${h}" rx="2"/>
      <text class="pv-name" x="14" y="22">${escapeHtml(truncate(port.name, 24))}</text>
      <text class="pv-sub" x="14" y="38">${port.kind} port</text></g>`);
  }
  for (const remote of group.remotes || []) {
    const [w, h] = sizes.remote;
    add(remote.id, remote.x, remote.y, w, h);
    shapes.push(`<g class="pv-port" transform="translate(${remote.x} ${remote.y})">
      <rect class="pv-box" width="${w}" height="${h}" rx="2" stroke-dasharray="6 4"/>
      <text class="pv-name" x="20" y="40">${escapeHtml(truncate(remote.name, 34))}</text>
      <text class="pv-sub" x="20" y="66">remote process group</text></g>`);
  }
  for (const funnel of group.funnels) {
    const [w, h] = sizes.funnel;
    add(funnel.id, funnel.x, funnel.y, w, h);
    shapes.push(`<g class="pv-funnel"><title>Funnel</title><circle cx="${funnel.x + w / 2}" cy="${funnel.y + h / 2}" r="${w / 2}"/></g>`);
  }

  const edges = [];
  for (const conn of group.connections) {
    const src = boxes.get(conn.source.id) || boxes.get(conn.source.groupId);
    const dst = boxes.get(conn.destination.id) || boxes.get(conn.destination.groupId);
    if (!src || !dst) continue;
    edges.push(edgePath(src, dst, conn));
  }

  preview.bounds = boundsOf([...boxes.values()]);
  const empty = boxes.size ? "" : `<text x="24" y="40" class="pv-sub">This process group is empty.</text>`;
  previewSvg.innerHTML = `<defs><marker id="pv-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="pv-arrow" d="M0 0L10 5L0 10z"/></marker></defs>
    <g>${edges.join("")}</g><g>${shapes.join("")}</g>${empty}`;
}

function edgePath(src, dst, conn) {
  const label = escapeHtml(truncate((conn.relationships || []).join(", ") || conn.name, 28));
  if (src === dst) {
    const x = src.x + src.w;
    const y = src.y + src.h / 2;
    return `<g class="pv-conn"><path d="M${x} ${y - 24} C${x + 70} ${y - 24} ${x + 70} ${y + 24} ${x} ${y + 24}" marker-end="url(#pv-arrow)"/><text x="${x + 74}" y="${y + 4}">${label}</text></g>`;
  }
  const centre = (b) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
  const points = [centre(src), ...(conn.bends || []), centre(dst)];
  points[0] = edgePoint(src, points[1]);
  points[points.length - 1] = edgePoint(dst, points[points.length - 2]);
  const d = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const mid = Math.floor((points.length - 1) / 2);
  const lx = (points[mid].x + points[mid + 1].x) / 2;
  const ly = (points[mid].y + points[mid + 1].y) / 2;
  return `<g class="pv-conn"><path d="${d}" marker-end="url(#pv-arrow)"/><text x="${lx.toFixed(1)}" y="${(ly - 6).toFixed(1)}" text-anchor="middle">${label}</text></g>`;
}

// Where the line from a box's centre towards `toward` leaves the box.
function edgePoint(box, toward) {
  const cx = box.x + box.w / 2;
  const cy = box.y + box.h / 2;
  const dx = toward.x - cx;
  const dy = toward.y - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const t = Math.min(dx ? box.w / 2 / Math.abs(dx) : Infinity, dy ? box.h / 2 / Math.abs(dy) : Infinity);
  return { x: cx + dx * t, y: cy + dy * t };
}

function boundsOf(boxes) {
  if (!boxes.length) return { x: 0, y: 0, w: 600, h: 300 };
  const minX = Math.min(...boxes.map((b) => b.x));
  const minY = Math.min(...boxes.map((b) => b.y));
  const maxX = Math.max(...boxes.map((b) => b.x + b.w));
  const maxY = Math.max(...boxes.map((b) => b.y + b.h));
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

function fitPreview() {
  if (!preview.data || !preview.bounds) return;
  const rect = previewStage.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const pad = 80;
  const b = preview.bounds;
  // Never magnify past 0.75 screen pixels per canvas unit, so a group with one
  // or two components is not blown up to fill the stage.
  const maxScale = 0.75;
  let w = Math.max(b.w + pad * 2, rect.width / maxScale);
  let h = Math.max(b.h + pad * 2, rect.height / maxScale);
  // Match the stage's aspect ratio so screen-to-canvas maths stays linear.
  const aspect = rect.width / rect.height;
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;
  setView({ x: b.x + b.w / 2 - w / 2, y: b.y + b.h / 2 - h / 2, w, h });
}

function setView(view) {
  preview.view = view;
  previewSvg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
}

function zoomAt(factor, clientX, clientY) {
  if (!preview.view) return;
  const rect = previewStage.getBoundingClientRect();
  const rx = clientX === undefined ? 0.5 : (clientX - rect.left) / rect.width;
  const ry = clientY === undefined ? 0.5 : (clientY - rect.top) / rect.height;
  const v = preview.view;
  const limit = Math.max(preview.bounds.w, preview.bounds.h, 600) * 6;
  const w = Math.min(Math.max(v.w * factor, 240), limit);
  const h = (w / v.w) * v.h;
  const px = v.x + rx * v.w;
  const py = v.y + ry * v.h;
  setView({ x: px - rx * w, y: py - ry * h, w, h });
}

previewStage.addEventListener(
  "wheel",
  (event) => {
    if (!preview.view) return;
    event.preventDefault();
    zoomAt(Math.exp(event.deltaY * 0.0015), event.clientX, event.clientY);
  },
  { passive: false }
);

previewStage.addEventListener("pointerdown", (event) => {
  if (!preview.view) return;
  preview.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  previewStage.setPointerCapture(event.pointerId);
  preview.drag = { moved: false, target: event.target };
});

previewStage.addEventListener("pointermove", (event) => {
  const last = preview.pointers.get(event.pointerId);
  if (!last || !preview.view) return;
  const rect = previewStage.getBoundingClientRect();
  if (preview.pointers.size === 2) {
    const [a, b] = [...preview.pointers.values()];
    const before = Math.hypot(a.x - b.x, a.y - b.y);
    preview.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const [c, d] = [...preview.pointers.values()];
    const after = Math.hypot(c.x - d.x, c.y - d.y);
    if (before && after) zoomAt(before / after, (c.x + d.x) / 2, (c.y + d.y) / 2);
    preview.drag.moved = true;
    return;
  }
  const dx = event.clientX - last.x;
  const dy = event.clientY - last.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) preview.drag.moved = true;
  if (!preview.drag.moved) return;
  previewStage.classList.add("panning");
  preview.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const v = preview.view;
  setView({ ...v, x: v.x - (dx / rect.width) * v.w, y: v.y - (dy / rect.height) * v.h });
});

function endPointer(event) {
  if (!preview.pointers.has(event.pointerId)) return;
  preview.pointers.delete(event.pointerId);
  previewStage.classList.remove("panning");
  if (event.type === "pointerup" && preview.drag && !preview.drag.moved) {
    const group = preview.drag.target.closest?.(".pv-group");
    if (group) openGroup(group.dataset.group);
  }
  if (!preview.pointers.size) preview.drag = null;
}
previewStage.addEventListener("pointerup", endPointer);
previewStage.addEventListener("pointercancel", endPointer);

previewSvg.addEventListener("keydown", (event) => {
  const group = event.target.closest?.(".pv-group");
  if (group && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    openGroup(group.dataset.group);
  }
});

zoomInBtn.addEventListener("click", () => zoomAt(1 / 1.25));
zoomOutBtn.addEventListener("click", () => zoomAt(1.25));
fitBtn.addEventListener("click", fitPreview);
// When the stage changes size (window or splitter), keep the zoom level and the
// centre the user chose, and only stretch the view to the new aspect ratio.
let stageSize = null;
new ResizeObserver(([entry]) => {
  const { width, height } = entry.contentRect;
  const before = stageSize;
  stageSize = width && height ? { width, height } : null;
  if (!before || !stageSize || !preview.view) return;
  const v = preview.view;
  const w = v.w * (width / before.width);
  const h = v.h * (height / before.height);
  setView({ x: v.x + v.w / 2 - w / 2, y: v.y + v.h / 2 - h / 2, w, h });
}).observe(previewStage);

// ---------- fidelity ----------

function renderFidelity(report) {
  if (!report) {
    fidelityCount.textContent = "";
    fidelityBody.innerHTML = `<p class="panel-empty">The fidelity check could not run for this flow. See the log.</p>`;
    return;
  }
  fidelityCount.textContent = `${report.passed}/${report.total}`;
  const sourceNote =
    report.source === "xml"
      ? "The source side is read straight from the template XML, independently of the converter."
      : "Compared against the uploaded flow definition.";
  const intended = report.intended || {};
  const intendedParts = [
    intended.retyped ? `${intended.retyped} replaced type${intended.retyped === 1 ? "" : "s"}` : "",
    intended.renamedProperties ? `${intended.renamedProperties} renamed propert${intended.renamedProperties === 1 ? "y" : "ies"}` : "",
    intended.removedProperties ? `${intended.removedProperties} removed propert${intended.removedProperties === 1 ? "y" : "ies"}` : "",
  ].filter(Boolean);
  fidelityBody.innerHTML = `
    <div class="fidelity-head ${report.allPassed ? "all-pass" : "has-fail"}">
      <b>${report.passed} of ${report.total}</b><span>checks passed</span>
    </div>
    <p class="hint">${escapeHtml(sourceNote)}${intendedParts.length ? ` Intended changes are not counted as drift: ${escapeHtml(intendedParts.join(", "))}.` : ""}</p>
    <ul class="check-list">
      ${report.checks
        .map((c) => {
          const count = c.detail || `${c.checked - c.failed}/${c.checked}`;
          const examples = c.examples?.length
            ? `<span class="check-detail">${c.examples.map(escapeHtml).join("<br>")}${c.failed > c.examples.length ? `<br>…and ${c.failed - c.examples.length} more` : ""}</span>`
            : "";
          return `<li class="check ${c.passed ? "check-ok" : "check-fail"}">
            <span class="check-mark" aria-label="${c.passed ? "passed" : "failed"}">${c.passed ? "✓" : "✗"}</span>
            <span>${escapeHtml(c.label)}</span>
            <span class="check-count">${escapeHtml(count)}</span>
            ${examples}
          </li>`;
        })
        .join("")}
    </ul>
    ${(report.notes || []).map((n) => notice("Note", n, false)).join("")}`;
}

// ---------- checklist ----------

function checklistKey() {
  const u = state.upload;
  return u ? `${CHECKLIST_KEY}:${u.filename}|${u.size}|${targetEl.value}` : "";
}

function loadTicks() {
  try {
    return JSON.parse(localStorage.getItem(checklistKey()) || "{}");
  } catch {
    return {};
  }
}

function buildChecklist() {
  const analysis = state.analysis;
  if (!analysis) return [];
  const summary = analysis.summary || {};
  const doc = analysis.document || {};
  const targetMajor = Number(String(summary.targetVersion || "").split(".")[0]);
  const isXml = Boolean(state.artifacts?.migratedXml) || (!state.artifacts && selectedOutputFormat() === "xml_template");
  const fileName = state.artifacts?.migratedFlow?.name || state.artifacts?.migratedXml?.name || "the migrated file";
  const readiness = state.readiness?.items || [];

  const importSteps = [
    {
      id: "step:import",
      text: isXml
        ? `Upload ${fileName} as a template (Operate palette → Upload template), then drag a Template onto the canvas.`
        : `Drag a Process Group onto the NiFi canvas, choose Browse, and upload ${fileName}.`,
    },
  ];

  const fixes = [];
  for (const f of analysis.findings || []) {
    if (!(f.manualReview || f.outcome === "removed" || f.outcome === "manual_review")) continue;
    fixes.push({
      id: `review:${f.identifier}`,
      text: `${f.name} (${shortType(f.sourceType)}): ${OUTCOME_LABELS[f.outcome] || f.outcome}`,
      note: f.recommendation || f.explanation,
    });
  }
  for (const item of readiness) {
    fixes.push({
      id: `ready:${item.kind}:${item.componentId}`,
      text: `${item.component}: ${item.title.toLowerCase()}`,
      note: `${item.detail}. ${item.fix}`,
    });
  }
  if (doc.variables && targetMajor >= 2) {
    fixes.push({
      id: "step:variables",
      text: `Recreate ${doc.variables} group variable${doc.variables === 1 ? "" : "s"} as Parameter Context parameters.`,
      note: "NiFi 2.x removed the Variable Registry; ${var} references resolve to nothing until you do.",
    });
  }

  const start = [];
  if (doc.componentCounts?.controllerService) {
    start.push({ id: "step:services", text: "Enable the controller services in the imported group." });
  }
  start.push({ id: "step:start", text: "Start the processors and watch the first FlowFiles through each connection." });

  return [
    { title: "Import", items: importSteps },
    { title: "Fix before starting", items: fixes },
    { title: "Start", items: start },
  ].filter((g) => g.items.length);
}

function renderChecklist() {
  const groups = buildChecklist();
  if (!groups.length) {
    checklistBody.innerHTML = `<p class="panel-empty">Analyze a flow to build its import checklist.</p>`;
    checklistProgress.textContent = "";
    checklistCount.textContent = "";
    return;
  }
  const ticks = loadTicks();
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  const done = groups.reduce((n, g) => n + g.items.filter((i) => ticks[i.id]).length, 0);
  checklistProgress.textContent = `${done} of ${total} done · ticks are saved in this browser`;
  checklistCount.textContent = `${done}/${total}`;
  checklistBody.innerHTML = groups
    .map(
      (g) => `<section class="checklist-group">
        <h3>${escapeHtml(g.title)}</h3>
        <ul class="checklist">${g.items
          .map(
            (item) => `<li class="${ticks[item.id] ? "done" : ""}"><label>
              <input type="checkbox" data-task="${escapeHtml(item.id)}"${ticks[item.id] ? " checked" : ""} />
              <span class="task-text">${escapeHtml(item.text)}${item.note ? `<small>${escapeHtml(item.note)}</small>` : ""}</span>
            </label></li>`
          )
          .join("")}</ul>
      </section>`
    )
    .join("");
}

checklistBody.addEventListener("change", (event) => {
  const box = event.target.closest("input[data-task]");
  if (!box || !checklistKey()) return;
  const ticks = loadTicks();
  if (box.checked) ticks[box.dataset.task] = true;
  else delete ticks[box.dataset.task];
  localStorage.setItem(checklistKey(), JSON.stringify(ticks));
  renderChecklist();
});

printBtn.addEventListener("click", () => window.print());

// ---------- helpers ----------

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shortType(type) {
  return String(type || "").split(".").pop();
}

function truncate(text, max) {
  const s = String(text || "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ---------- wiring ----------

fileEl.addEventListener("change", async () => {
  const file = fileEl.files?.[0];
  if (!file) return;
  try {
    await readMigrationUpload(file);
  } catch (err) {
    reportError(err);
  } finally {
    fileEl.value = "";
  }
});

["dragenter", "dragover"].forEach((name) => {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag");
  });
});
["dragleave", "drop"].forEach((name) => {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag");
  });
});
dropzone.addEventListener("drop", async (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  try {
    await readMigrationUpload(file);
  } catch (err) {
    reportError(err);
  }
});

document.querySelectorAll(".js-load-sample").forEach((button) => {
  button.addEventListener("click", () => loadSample().catch(reportError));
});

pasteToggle.addEventListener("click", () => {
  pastePanel.classList.toggle("hidden");
  if (!pastePanel.classList.contains("hidden")) pasteInput.focus();
});
pasteCancel.addEventListener("click", () => {
  pasteInput.value = "";
  pastePanel.classList.add("hidden");
});
pasteSubmit.addEventListener("click", () => {
  const text = pasteInput.value.trim();
  if (!text) return;
  loadText(text)
    .then(() => {
      pasteInput.value = "";
      pastePanel.classList.add("hidden");
    })
    .catch(reportError);
});

// Pasting a template or flow definition anywhere outside a text field loads it.
document.addEventListener("paste", (event) => {
  const target = event.target;
  if (target.closest?.("input, textarea, select, [contenteditable]")) return;
  const text = event.clipboardData?.getData("text") || "";
  const trimmed = text.trim();
  if (!trimmed.startsWith("<") && !trimmed.startsWith("{")) return;
  event.preventDefault();
  log("Loading flow from the clipboard.");
  loadText(trimmed).catch(reportError);
});

analyzeBtn.addEventListener("click", () => runMigration(false).catch(reportError));
generateBtn.addEventListener("click", () => {
  const download = migratedFile() && artifactsEl.querySelector(".primary-download");
  if (download) {
    download.click();
    log(`Downloaded ${migratedFile().name}.`);
    return;
  }
  runMigration(true).catch(reportError);
});

function rerunAnalysis() {
  if (state.upload && !state.running) runMigration(false).catch(reportError);
}
sourceEl.addEventListener("change", rerunAnalysis);
targetEl.addEventListener("change", () => {
  syncXmlAvailability();
  rerunAnalysis();
});
xmlRadio.addEventListener("change", rerunAnalysis);
jsonRadio.addEventListener("change", rerunAnalysis);

resetPreview();
loadMigrationVersions().catch((err) => log(`Could not load versions: ${err.message || err}`, "error"));
