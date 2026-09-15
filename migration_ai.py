"""AI assistance for the migration analysis, plus the Migration Report renderer.

The AI's job here is deliberately narrow. `migration_engine` has already
classified every component from the live catalog and the curated rule base; what
it cannot do is explain an unfamiliar processor or propose a replacement for one
nobody wrote a rule for. That gap — and only that gap — is what we ask a model
about.

Two guardrails make this safe:

  * We ask about the `unknown` / `manual_review` findings only. Components the
    catalog or rules already settled are never sent for a second opinion, so the
    model cannot overturn a deterministic verdict.
  * The reply is merged into `ComponentFinding.ai_suggestion` (a display-only
    field) and the finding stays flagged for review. `generate_migrated_flow`
    reads `outcome`/`target_type`, never `ai_suggestion`, so no AI text can
    silently rewrite anyone's flow.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from migration_engine import ComponentFinding, MigrationAnalysis
from migration_rules import NEEDS_INVESTIGATION

log = logging.getLogger(__name__)

#: Cap how many findings go into one prompt. A big flow can have hundreds of
#: unknowns and the marginal value of the 40th explanation is near zero.
MAX_FINDINGS_PER_PROMPT = 40


def needs_ai_review(analysis: MigrationAnalysis) -> list[ComponentFinding]:
    """The findings worth asking a model about: unverified or human-flagged.

    Uses `NEEDS_INVESTIGATION` rather than `NEEDS_REVIEW` because an unverified
    component is exactly the case where a model adds value, even though it is
    not reported to the user as a known defect.
    """
    out = [f for f in analysis.findings if f.outcome in NEEDS_INVESTIGATION or f.manual_review]
    # De-duplicate by source type: ten copies of the same processor need one answer.
    seen: set[str] = set()
    unique: list[ComponentFinding] = []
    for f in out:
        key = f.source_type.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique[:MAX_FINDINGS_PER_PROMPT]


def build_migration_prompt(
    analysis: MigrationAnalysis,
    findings: list[ComponentFinding],
    catalog_summary: dict[str, Any] | None = None,
) -> str:
    """Build the prompt asking a model to fill the gaps the rule base left.

    The prompt is explicit that the deterministic verdicts are not up for
    debate, and demands a strict JSON reply so the answer can be merged
    field-by-field instead of pasted at the user as prose.
    """
    rows = []
    for f in findings:
        rows.append(
            {
                "component": f.name,
                "sourceType": f.source_type,
                "kind": f.kind,
                "currentVerdict": f.outcome,
                "whyFlagged": f.explanation,
            }
        )

    available = ""
    if catalog_summary:
        procs = catalog_summary.get("allowedProcessorTypes") or []
        if procs:
            short = sorted({str(p).rsplit(".", 1)[-1] for p in procs})
            available = (
                "\nProcessor types confirmed installed on the target instance "
                f"({len(short)} total). Only propose replacements from this list:\n"
                + ", ".join(short)
                + "\n"
            )

    return f"""You are a NiFi upgrade specialist helping migrate a flow from Apache NiFi \
{analysis.source_version} to Apache NiFi {analysis.target_version}.

A deterministic analyser has already checked this flow against the target version's
installed component catalog and a curated rule base. Its verdicts are authoritative
and you must not contradict them. Your task is only to explain and advise on the
components below, which the analyser could not resolve on its own.

Components needing your input:
{json.dumps(rows, indent=2)}
{available}
For each component, answer these questions:
  - Does this component still exist in NiFi {analysis.target_version}? If it was removed
    or renamed, say so.
  - If it needs replacing, which target-version processor replaces it, and how do the
    properties and relationships differ?
  - Are there new mandatory properties in the target version that a {analysis.source_version}
    flow would not have set?
  - Does the migration need a human decision (a semantic change, not a mechanical rename)?

Reply with ONLY a JSON array, no prose and no code fences. One object per component:

[
  {{
    "sourceType": "<exactly the sourceType you were given>",
    "existsInTarget": true | false | "unknown",
    "recommendation": "<one or two sentences of concrete advice>",
    "suggestedReplacement": "<target processor short name, or null>",
    "propertyChanges": [{{"from": "<old property>", "to": "<new property>", "reason": "<why>"}}],
    "newRequiredProperties": ["<property name>"],
    "manualReview": true | false,
    "confidence": "high" | "medium" | "low"
  }}
]

Rules for your answer:
  - If you are not confident a component exists in {analysis.target_version}, set
    existsInTarget to "unknown" and confidence to "low". Do not guess.
  - Never invent a processor name. If no replacement exists, use null.
  - Set manualReview to true whenever the change alters behaviour rather than just
    renaming something.
"""


def parse_ai_response(text: str) -> list[dict[str, Any]]:
    """Parse the model's JSON array, tolerating code fences and stray prose.

    Returns an empty list rather than raising: AI enrichment is optional, and a
    malformed reply must not fail an analysis that is already complete and valid
    without it.
    """
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        log.warning("AI migration reply contained no JSON array")
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        log.warning("AI migration reply was not valid JSON: %s", exc)
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def apply_ai_suggestions(analysis: MigrationAnalysis, suggestions: list[dict[str, Any]]) -> int:
    """Attach AI advice to matching findings. Returns how many were annotated.

    Only ever writes to display-only fields (`ai_suggestion`, and `recommendation`
    when the rule base had nothing to say). The `outcome` a deterministic check
    produced is never changed, and `target_type` — which the generator acts on —
    is left alone entirely.
    """
    if not suggestions:
        return 0

    # Index by both the full class name and the short name, because the model
    # may echo either back.
    by_type: dict[str, dict[str, Any]] = {}
    for item in suggestions:
        key = str(item.get("sourceType") or "").strip().lower()
        if not key:
            continue
        by_type[key] = item
        by_type.setdefault(key.rsplit(".", 1)[-1], item)

    annotated = 0
    for finding in analysis.findings:
        for key in (finding.source_type.lower(), finding.source_type.rsplit(".", 1)[-1].lower()):
            item = by_type.get(key)
            if not item:
                continue

            parts: list[str] = []
            exists = item.get("existsInTarget")
            if exists is False:
                parts.append(f"AI: not available in NiFi {analysis.target_version}.")
            elif exists == "unknown":
                parts.append("AI: could not confirm availability in the target version.")

            rec = str(item.get("recommendation") or "").strip()
            if rec:
                parts.append(rec)

            replacement = item.get("suggestedReplacement")
            if replacement:
                parts.append(f"Suggested replacement: {replacement} (verify before applying).")

            new_props = [str(p) for p in (item.get("newRequiredProperties") or []) if p]
            if new_props:
                parts.append("New required properties: " + ", ".join(new_props))
                for prop in new_props:
                    if prop not in finding.new_required_properties:
                        finding.new_required_properties.append(prop)

            confidence = str(item.get("confidence") or "").strip()
            if confidence:
                parts.append(f"[AI confidence: {confidence}]")

            for change in item.get("propertyChanges") or []:
                if not isinstance(change, dict):
                    continue
                entry = {
                    "property": str(change.get("from") or ""),
                    "from": str(change.get("from") or ""),
                    "to": str(change.get("to") or ""),
                    "reason": f"AI suggestion — {change.get('reason') or 'verify before applying'}",
                }
                if entry["property"] and entry not in finding.property_changes:
                    finding.property_changes.append(entry)

            if parts:
                finding.ai_suggestion = " ".join(parts)
                # Keep the deterministic recommendation when we have one; only
                # fill the field if the rule base left it blank.
                if not finding.recommendation and rec:
                    finding.recommendation = f"AI suggestion (verify): {rec}"
                if item.get("manualReview"):
                    finding.manual_review = True
                annotated += 1
            break

    return annotated


# --- Migration report ---------------------------------------------------------


def build_report(
    analysis: MigrationAnalysis,
    generation: Any | None = None,
    ai_used: str | None = None,
) -> dict[str, Any]:
    """The machine-readable Migration Report (downloaded as JSON)."""
    summary = analysis.summary()
    report: dict[str, Any] = {
        "report": "NiFi Migration Report",
        "generatedBy": "Flow Studio",
        "sourceVersion": analysis.source_version,
        "targetVersion": analysis.target_version,
        "sourceFile": analysis.filename,
        "sourceFormat": analysis.source_format,
        "aiModel": ai_used,
        "catalogVersion": analysis.catalog_version,
        "summary": summary,
        "components": {
            "compatible": [f.to_dict() for f in analysis.findings if f.outcome == "compatible"],
            "configChanges": [
                f.to_dict() for f in analysis.findings if f.outcome == "config_change"
            ],
            "replaced": [
                f.to_dict() for f in analysis.findings if f.outcome in ("replaced", "renamed")
            ],
            "deprecated": [f.to_dict() for f in analysis.findings if f.outcome == "deprecated"],
            "removed": [f.to_dict() for f in analysis.findings if f.outcome == "removed"],
            "unverified": [f.to_dict() for f in analysis.findings if f.outcome == "unknown"],
        },
        "propertyChanges": [
            {
                "component": f.name,
                "sourceType": f.source_type,
                "changes": f.property_changes,
            }
            for f in analysis.findings
            if f.property_changes
        ],
        "manualInterventionRequired": [f.to_dict() for f in analysis.findings_needing_review()],
        "flowLevelConcerns": analysis.concerns,
        "aiRecommendations": [
            {"component": f.name, "sourceType": f.source_type, "suggestion": f.ai_suggestion}
            for f in analysis.findings
            if f.ai_suggestion
        ],
        "warnings": analysis.warnings,
        "errors": analysis.errors,
    }
    if generation is not None:
        report["generation"] = generation.to_dict() if hasattr(generation, "to_dict") else generation
    return report


def render_report_markdown(report: dict[str, Any]) -> str:
    """Human-readable Migration Report, for the second download button.

    Markdown rather than PDF: it renders in every browser and diffs cleanly in
    the ticket someone will inevitably attach it to.
    """
    s = report.get("summary") or {}
    lines: list[str] = [
        "# NiFi Migration Report",
        "",
        f"- **Source version:** {report.get('sourceVersion')}",
        f"- **Target version:** {report.get('targetVersion')}",
        f"- **Source file:** `{report.get('sourceFile')}`",
        f"- **File format:** {report.get('sourceFormat')}",
        f"- **AI model:** {report.get('aiModel') or 'not used'}",
        f"- **Verified against live NiFi catalog:** {report.get('catalogVersion') or 'no (rule base only)'}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | --- |",
        f"| Components analysed | {s.get('totalComponents', 0)} |",
        f"| Compatible | {s.get('compatible', 0)} |",
        f"| Require configuration changes | {s.get('configChanges', 0)} |",
        f"| Replaced / renamed | {s.get('replaced', 0)} |",
        f"| Deprecated | {s.get('deprecated', 0)} |",
        f"| Removed / unsupported | {s.get('removed', 0)} |",
        f"| Not verified | {s.get('unknown', 0)} |",
        f"| **Manual review required** | **{s.get('manualReview', 0)}** |",
        "",
    ]

    concerns = report.get("flowLevelConcerns") or []
    if concerns:
        lines += ["## Flow-level concerns", ""]
        for c in concerns:
            lines += [
                f"### {c.get('title')}",
                "",
                str(c.get("explanation") or ""),
                "",
                f"**Recommendation:** {c.get('recommendation') or ''}",
                "",
            ]

    manual = report.get("manualInterventionRequired") or []
    if manual:
        lines += [
            "## Manual intervention required",
            "",
            "These components were **not** modified in the generated flow. They are marked "
            "in the output so nothing was changed on a guess.",
            "",
            "| Component | Current type | Outcome | Suggested target | Why |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in manual:
            lines.append(
                f"| {f.get('name')} | `{_short(f.get('sourceType'))}` | {f.get('outcome')} "
                f"| {f.get('targetType') or '—'} | {_cell(f.get('explanation'))} |"
            )
        lines.append("")

    changed = (report.get("components") or {}).get("configChanges") or []
    replaced = (report.get("components") or {}).get("replaced") or []
    if changed or replaced:
        lines += [
            "## Components requiring changes",
            "",
            "| Component | Current type | Source | Target | Changes required | Recommended migration | Manual review |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for f in list(replaced) + list(changed):
            changes = "; ".join(
                f"{c.get('from')} → {c.get('to')}" for c in (f.get("propertyChanges") or [])
            ) or _cell(f.get("explanation"))
            lines.append(
                f"| {f.get('name')} | `{_short(f.get('sourceType'))}` "
                f"| {report.get('sourceVersion')} | {report.get('targetVersion')} "
                f"| {changes} | {_cell(f.get('recommendation')) or '—'} "
                f"| {'Yes' if f.get('manualReview') else 'No'} |"
            )
        lines.append("")

    prop_changes = report.get("propertyChanges") or []
    if prop_changes:
        lines += ["## Property changes", "", "| Component | Property | From | To | Reason |", "| --- | --- | --- | --- | --- |"]
        for entry in prop_changes:
            for c in entry.get("changes") or []:
                lines.append(
                    f"| {entry.get('component')} | {c.get('property')} | {c.get('from')} "
                    f"| {c.get('to')} | {_cell(c.get('reason'))} |"
                )
        lines.append("")

    ai_recs = report.get("aiRecommendations") or []
    if ai_recs:
        lines += [
            "## AI recommendations",
            "",
            f"Produced by `{report.get('aiModel')}`. These are suggestions for review, not "
            "applied changes — the generated flow contains none of them.",
            "",
        ]
        for rec in ai_recs:
            lines += [f"- **{rec.get('component')}** (`{_short(rec.get('sourceType'))}`): {rec.get('suggestion')}"]
        lines.append("")

    deprecated = (report.get("components") or {}).get("deprecated") or []
    if deprecated:
        lines += ["## Deprecated components", ""]
        for f in deprecated:
            lines.append(f"- **{f.get('name')}** (`{_short(f.get('sourceType'))}`): {_cell(f.get('explanation'))}")
        lines.append("")

    unverified = (report.get("components") or {}).get("unverified") or []
    if unverified:
        lines += [
            "## Not verified",
            "",
            "No migration rule covers these and no target-version NiFi was connected to "
            "confirm they are installed. They are most likely fine, but this run did not "
            "prove it.",
            "",
        ]
        for f in unverified:
            lines.append(f"- **{f.get('name')}** (`{_short(f.get('sourceType'))}`)")
        lines.append("")

    generation = report.get("generation") or {}
    if generation:
        lines += [
            "## Generated flow",
            "",
            f"- Output format: {generation.get('outputFormat')}",
            f"- Changes applied: {generation.get('appliedCount', 0)}",
            f"- Left unchanged for review: {generation.get('skippedCount', 0)}",
            f"- Manual-review markers written into the flow: {generation.get('manualCount', 0)}",
            "",
        ]
        applied = generation.get("appliedChanges") or []
        if applied:
            lines += ["| Component | Change | From | To |", "| --- | --- | --- | --- |"]
            for c in applied:
                lines.append(
                    f"| {c.get('component')} | {c.get('change')} | {c.get('from')} | {c.get('to')} |"
                )
            lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]

    errors = report.get("errors") or []
    if errors:
        lines += ["## Errors", ""] + [f"- {e}" for e in errors] + [""]

    compatible = (report.get("components") or {}).get("compatible") or []
    lines += [
        "## Compatible components",
        "",
        f"{len(compatible)} component(s) migrate without changes.",
        "",
    ]
    for f in compatible[:200]:
        lines.append(f"- {f.get('name')} (`{_short(f.get('sourceType'))}`)")
    if len(compatible) > 200:
        lines.append(f"- …and {len(compatible) - 200} more")

    return "\n".join(lines) + "\n"


def _short(type_name: Any) -> str:
    return str(type_name or "").rsplit(".", 1)[-1]


def _cell(text: Any) -> str:
    """Flatten text for a markdown table cell."""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()
