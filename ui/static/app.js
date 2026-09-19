const fileEl = document.getElementById("migration-file");
const dropzone = document.getElementById("dropzone");
const metaEl = document.getElementById("migration-meta");
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
const logEl = document.getElementById("log");
const exportBtn = document.getElementById("export-logs");
const reportEl = document.getElementById("migration-report");
const reportTitle = document.getElementById("migration-report-title");
const reportPill = document.getElementById("migration-report-pill");
const statsEl = document.getElementById("migration-stats");
const concernsEl = document.getElementById("migration-concerns");
const detailsEl = document.getElementById("migration-details");
const warningsEl = document.getElementById("migration-warnings");
const artifactsEl = document.getElementById("migration-artifacts");
const themeToggle = document.getElementById("theme-toggle");
const themeLabel = document.getElementById("theme-toggle-label");

const THEME_KEY = "flowgenix.theme";
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

let migrationUpload = null;
let migrationVersions = [];
let logLines = [];
let artifactUrls = [];

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeLabel.textContent = theme === "dark" ? "Light" : "Dark";
  localStorage.setItem(THEME_KEY, theme);
}

applyTheme(localStorage.getItem(THEME_KEY) || "light");
themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

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

function resetMigrationReport() {
  for (const url of artifactUrls) URL.revokeObjectURL(url);
  artifactUrls = [];
  reportEl.classList.add("hidden");
  statsEl.innerHTML = "";
  concernsEl.innerHTML = "";
  detailsEl.innerHTML = "";
  warningsEl.innerHTML = "";
  artifactsEl.innerHTML = "";
  artifactsEl.classList.add("hidden");
}

async function readMigrationUpload(file) {
  resetMigrationReport();
  const text = await file.text();
  const resp = await fetch("/api/migration/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_text: text, source_filename: file.name }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || "Could not inspect the file");
  migrationUpload = { text, filename: file.name, info: data };
  renderMigrationDetection(data);
  analyzeBtn.disabled = false;
  generateBtn.disabled = false;
  log(`Inspected ${file.name}: ${data.sourceFormat}, ${data.totalComponents} component(s).`);
}

function renderMigrationDetection(info) {
  detectEl.classList.remove("hidden");
  formatPill.textContent = FORMAT_LABELS[info.sourceFormat] || info.sourceFormat;
  versionPill.textContent = info.detectedVersion
    ? `NiFi ${info.detectedVersion} (${info.detectionConfidence || "detected"})`
    : `version unknown${info.detectedLine ? ` · ${info.detectedLine}` : ""}`;
  countPill.textContent = `${info.totalComponents || 0} components`;
  evidenceEl.textContent = (info.detectionEvidence || []).join(" ");
  if (info.detectedVersion) sourceEl.value = info.detectedVersion;
  metaEl.textContent = `${info.filename} · ${FORMAT_LABELS[info.sourceFormat] || info.sourceFormat}`;
}

function migrationRequestBody() {
  if (!migrationUpload) throw new Error("Upload a flow file first.");
  return {
    source_text: migrationUpload.text,
    source_filename: migrationUpload.filename,
    source_version: sourceEl.value || null,
    target_version: targetEl.value,
    output_format: selectedOutputFormat(),
  };
}

async function runMigration(generate) {
  resetMigrationReport();
  reportEl.classList.remove("hidden");
  reportPill.textContent = generate ? "generating…" : "analysing…";
  const body = migrationRequestBody();
  log(
    `${generate ? "Generating" : "Analysing"} ${body.source_filename} for ${body.source_version || "detected"} → ${body.target_version} as ${FORMAT_LABELS[body.output_format]}.`
  );
  const path = generate ? "/api/migration/generate" : "/api/migration/analyze";
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || err.message || "Migration request failed");
  }
  await consumeMigrationStream(resp.body);
}

async function consumeMigrationStream(stream) {
  const reader = stream.getReader();
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
      const event = JSON.parse(line.slice(5).trim());
      if (event.type === "status") log(event.message);
      if (event.type === "error") {
        log(event.message, "error");
        reportPill.textContent = "failed";
      }
      if (event.type === "analysis") renderMigrationReport(event.analysis);
      if (event.type === "generated") {
        renderMigrationReport(event.analysis);
        renderMigrationArtifacts(event.artifacts || {});
        reportPill.textContent = "generated";
        log("Migrated flow is ready to download.");
      }
    }
  }
}

function renderMigrationReport(analysis) {
  const summary = analysis.summary || {};
  reportTitle.textContent = `NiFi ${summary.sourceVersion} → ${summary.targetVersion}`;
  reportPill.textContent = summary.manualReview ? "review needed" : "ready";
  const stats = [
    ["Components", summary.totalComponents],
    ["Compatible", summary.compatible],
    ["Config changes", summary.configChanges],
    ["Removed", summary.removed],
    ["Not verified", summary.unknown],
    ["Manual review", summary.manualReview],
  ];
  statsEl.innerHTML = stats
    .map(([label, value]) => `<div class="stat"><b>${value ?? 0}</b><span>${label}</span></div>`)
    .join("");

  const concerns = analysis.concerns || [];
  concernsEl.classList.toggle("hidden", concerns.length === 0);
  concernsEl.innerHTML = concerns
    .map(
      (c) =>
        `<div class="concern"><strong>${c.title}</strong><p>${c.explanation || ""}</p><p>${c.recommendation || ""}</p></div>`
    )
    .join("");

  const findings = (analysis.findings || []).filter((f) => f.outcome !== "compatible");
  if (!findings.length) {
    detailsEl.innerHTML = "<p class='hint'>Every component is compatible with the target version.</p>";
  } else {
    detailsEl.innerHTML = `
      <table class="migration-table">
        <thead><tr><th>Component</th><th>Type</th><th>Outcome</th><th>Notes</th></tr></thead>
        <tbody>
          ${findings
            .map((f) => {
              const type = (f.sourceType || "").split(".").pop();
              const label = OUTCOME_LABELS[f.outcome] || f.outcome;
              return `<tr>
                <td>${escapeHtml(f.name || "")}</td>
                <td><code>${escapeHtml(type)}</code></td>
                <td><span class="outcome-tag outcome-${escapeHtml(f.outcome)}">${escapeHtml(label)}</span></td>
                <td>${escapeHtml(f.explanation || f.recommendation || "")}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
  }

  const warnings = analysis.warnings || [];
  warningsEl.classList.toggle("hidden", warnings.length === 0);
  warningsEl.innerHTML = warnings.map((w) => `<div class="warning-item">${escapeHtml(w)}</div>`).join("");
}

function renderMigrationArtifacts(artifacts) {
  const links = [];
  if (artifacts.migratedFlow) {
    links.push(artifactAnchor(artifacts.migratedFlow, "Download JSON"));
  }
  if (artifacts.migratedXml) {
    links.push(artifactAnchor(artifacts.migratedXml, "Download XML"));
  }
  if (artifacts.reportMarkdown) {
    links.push(artifactAnchor(artifacts.reportMarkdown, "Report (Markdown)"));
  }
  if (artifacts.report) {
    const name = (artifacts.reportMarkdown?.name || "migration-report.md").replace(/\.md$/, ".json");
    links.push(
      artifactAnchor(
        { name, mediaType: "application/json", text: JSON.stringify(artifacts.report, null, 2) },
        "Report (JSON)"
      )
    );
  }
  artifactsEl.classList.toggle("hidden", links.length === 0);
  artifactsEl.innerHTML = links.join("");
}

// The server keeps nothing on disk, so each file arrives as text and becomes a
// download here.
function artifactAnchor(item, label) {
  const url = URL.createObjectURL(new Blob([item.text], { type: item.mediaType }));
  artifactUrls.push(url);
  const name = escapeHtml(item.name);
  return `<a href="${url}" download="${name}">${label} · ${name}</a>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

fileEl.addEventListener("change", async () => {
  const file = fileEl.files?.[0];
  if (!file) return;
  try {
    await readMigrationUpload(file);
  } catch (err) {
    log(String(err.message || err), "error");
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
    log(String(err.message || err), "error");
  }
});

analyzeBtn.addEventListener("click", () => runMigration(false).catch((err) => log(String(err.message || err), "error")));
generateBtn.addEventListener("click", () => runMigration(true).catch((err) => log(String(err.message || err), "error")));
sourceEl.addEventListener("change", resetMigrationReport);
targetEl.addEventListener("change", () => {
  resetMigrationReport();
  syncXmlAvailability();
});

loadMigrationVersions().catch((err) => log(`Could not load versions: ${err.message || err}`, "error"));
