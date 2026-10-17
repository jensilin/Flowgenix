"""Analyse a parsed flow against a target NiFi version, and generate a migrated copy.

Compatibility is decided offline and never depends on a language model's mood:

    1. **Curated rules** in `migration_rules.py` — known renames, removals,
       replacements, and property changes, each carrying an explanation.
    2. **Structural checks** — EVENT_DRIVEN on a 2.x target, `${var}` references
       when the Variable Registry is gone, and so on.
    3. Anything still unresolved is reported as `unknown` and flagged for review.

The generator honours that last point literally: a component whose outcome needs
review is copied through **unchanged** and marked, rather than being rewritten on
a guess. The uploaded file itself is never mutated — generation always works on a
deep copy.

Output format is a user choice constrained by the target line: XML templates are
only emitted for 1.x targets. JSON is restamped to the target dialect (2.6 vs
later 2.x field sets differ).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from flow_document import (
    FORMAT_JSON_SNAPSHOT,
    FORMAT_XML_TEMPLATE,
    Component,
    FlowDocument,
)
from flow_schema import NIFI_BUNDLE_GROUP, apply_json_dialect, follows_nifi_release
from migration_rules import (
    COMPATIBLE,
    CONFIG_CHANGE,
    DEPRECATED,
    MANUAL,
    NEEDS_REVIEW,
    OUTCOME_ORDER,
    REMOVED,
    RENAMED,
    REPLACED,
    UNKNOWN,
    LifecycleVerdict,
    Swap,
    downgrade_property_rules_for,
    flow_concerns,
    lifecycle_verdict,
    processor_rule_for,
    property_name_on,
    property_rules_for,
    service_rule_for,
)
from nifi_versions import get_version, is_downgrade, resolve_or_raise

log = logging.getLogger(__name__)

#: Expression-language reference to a Variable Registry entry, e.g. ${myVar}.
#: Matches a bare identifier only — ${filename:substringBefore('.')} and other
#: attribute expressions with functions are excluded, because those remain valid
#: on 2.x and rewriting them would corrupt the flow.
_BARE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.\-]*)\}")


@dataclass
class ComponentFinding:
    """The migration verdict for one component."""

    identifier: str
    name: str
    kind: str
    source_type: str
    outcome: str
    group_path: str = "/"
    target_type: str | None = None
    explanation: str = ""
    recommendation: str = ""
    property_changes: list[dict[str, str]] = field(default_factory=list)
    new_required_properties: list[str] = field(default_factory=list)
    removed_properties: list[str] = field(default_factory=list)
    #: Surface this component in the "manual review" bucket of the report.
    manual_review: bool = False
    #: Forbid the generator from rewriting this component at all.
    #:
    #: Deliberately separate from `manual_review`, because the two questions are
    #: different. A processor we could not verify still deserves a flag, but a
    #: known-safe property rename should still be applied to it — otherwise a
    #: user with no target instance to connect to gets a "migrated" flow in
    #: which nothing was actually migrated. Only a verdict with no valid
    #: mechanical rewrite (removed, or a rule that says the change is semantic)
    #: blocks generation.
    block_auto_migration: bool = False
    decided_by: str = "rules"  # rules | structural | default
    ai_suggestion: str = ""
    #: Changes Flowgenix proposes for this component. None is applied unless the
    #: user accepts its id when generating.
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "kind": self.kind,
            "sourceType": self.source_type,
            "targetType": self.target_type,
            "outcome": self.outcome,
            "groupPath": self.group_path,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "propertyChanges": self.property_changes,
            "newRequiredProperties": self.new_required_properties,
            "removedProperties": self.removed_properties,
            "manualReview": self.manual_review,
            "blockAutoMigration": self.block_auto_migration,
            "decidedBy": self.decided_by,
            "aiSuggestion": self.ai_suggestion,
            "suggestionIds": [s["id"] for s in self.suggestions],
        }


@dataclass
class MigrationAnalysis:
    """Everything the UI needs to render the compatibility report."""

    source_version: str
    target_version: str
    source_format: str
    filename: str
    findings: list[ComponentFinding] = field(default_factory=list)
    concerns: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    catalog_version: str | None = None
    document_summary: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        out = {outcome: 0 for outcome in OUTCOME_ORDER}
        for f in self.findings:
            out[f.outcome] = out.get(f.outcome, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        counts = self.counts()
        needs_review = sum(1 for f in self.findings if f.manual_review or f.outcome in NEEDS_REVIEW)
        return {
            "sourceVersion": self.source_version,
            "targetVersion": self.target_version,
            "sourceFormat": self.source_format,
            "filename": self.filename,
            "totalComponents": len(self.findings),
            "compatible": counts.get(COMPATIBLE, 0),
            "configChanges": counts.get(CONFIG_CHANGE, 0),
            "replaced": counts.get(REPLACED, 0) + counts.get(RENAMED, 0),
            "deprecated": counts.get(DEPRECATED, 0),
            "removed": counts.get(REMOVED, 0),
            "unknown": counts.get(UNKNOWN, 0),
            "manualReview": needs_review,
            "byOutcome": counts,
            "catalogVersion": self.catalog_version,
            "concernCount": len(self.concerns),
            "warningCount": len(self.warnings),
            "errorCount": len(self.errors),
            "direction": "downgrade"
            if is_downgrade(self.source_version, self.target_version)
            else "upgrade",
            "suggestionCount": len(self.suggestions()),
            "requiredSuggestionCount": sum(1 for s in self.suggestions() if s["required"]),
        }

    def suggestions(self) -> list[dict[str, Any]]:
        return [s for f in self.findings for s in f.suggestions]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "suggestions": self.suggestions(),
            "findings": [f.to_dict() for f in self.findings],
            "concerns": self.concerns,
            "warnings": self.warnings,
            "errors": self.errors,
            "document": self.document_summary,
        }

    def findings_needing_review(self) -> list[ComponentFinding]:
        """Components with a known problem a human must resolve.

        Excludes merely-unverified components; those are counted separately as
        `unknown` and listed under "not verified" in the report.
        """
        return [f for f in self.findings if f.manual_review or f.outcome in NEEDS_REVIEW]


# --- Analysis -----------------------------------------------------------------


def analyze_migration(
    doc: FlowDocument,
    source_version: str,
    target_version: str,
) -> MigrationAnalysis:
    """Classify every component in `doc` against `target_version`."""
    source = resolve_or_raise(source_version, "source")
    target = resolve_or_raise(target_version, "target")

    analysis = MigrationAnalysis(
        source_version=source.version,
        target_version=target.version,
        source_format=doc.source_format,
        filename=doc.filename,
        document_summary=doc.summary(),
    )

    if is_downgrade(source.version, target.version):
        analysis.warnings.append(
            f"Target {target.version} is older than source {source.version}. Downgrades are "
            "not a supported NiFi path: components added after the target release will "
            "have no equivalent. Review every finding carefully."
        )

    for comp in doc.components:
        if comp.kind == "processor":
            analysis.findings.append(_analyze_processor(comp, source, target))
        elif comp.kind == "controllerService":
            analysis.findings.append(_analyze_service(comp, source, target))
        else:
            # Ports, funnels, connections, labels, RPGs carry no type that can be
            # removed between releases; they migrate structurally.
            analysis.findings.append(
                ComponentFinding(
                    identifier=comp.identifier,
                    name=comp.name or comp.kind,
                    kind=comp.kind,
                    source_type=comp.type_name or comp.kind,
                    outcome=COMPATIBLE,
                    group_path=comp.group_path,
                    explanation=f"{comp.kind} carries no version-specific type and migrates as-is.",
                    decided_by="structural",
                )
            )

    for concern in flow_concerns(source.version, target.version, doc.source_format):
        analysis.concerns.append(
            {
                "key": concern.key,
                "title": concern.title,
                "outcome": concern.outcome,
                "explanation": concern.explanation,
                "recommendation": concern.recommendation,
            }
        )

    _check_variables(doc, source, target, analysis)
    _check_parameter_contexts(doc, source, target, analysis)

    return analysis


def _analyze_processor(
    comp: Component,
    source: Any,
    target: Any,
) -> ComponentFinding:
    finding = ComponentFinding(
        identifier=comp.identifier,
        name=comp.name or comp.short_type,
        kind="processor",
        source_type=comp.type_name,
        group_path=comp.group_path,
        outcome=COMPATIBLE,
    )

    rule = processor_rule_for(comp.type_name, target.version)
    life = lifecycle_verdict(comp.type_name, source.version, target.version)

    if rule is not None:
        finding.outcome = rule.outcome
        finding.explanation = rule.explanation
        finding.target_type = rule.replacement
        finding.manual_review = rule.manual_review
        finding.block_auto_migration = rule.manual_review or rule.outcome == REMOVED
        finding.decided_by = "rules"
        finding.new_required_properties = list(rule.new_required_properties)
        finding.removed_properties = [p for p in rule.removed_properties if p in comp.properties]
        if rule.replacement:
            finding.recommendation = f"Replace with {rule.replacement}."
        for old, new in rule.property_renames.items():
            if old in comp.properties:
                finding.property_changes.append(
                    {"property": old, "from": old, "to": new, "reason": "renamed in target version"}
                )
    elif life is not None:
        _apply_lifecycle(finding, life)
    else:
        finding.outcome = UNKNOWN
        finding.decided_by = "default"
        finding.explanation = (
            f"No migration rule covers {comp.short_type}. It is most likely unchanged "
            f"on NiFi {target.version}, but this has not been verified."
        )
        finding.recommendation = (
            f"Confirm {comp.short_type} exists on NiFi {target.version} before starting the flow."
        )
    _recommend_swap(finding, life)

    # 2. Property renames apply on top of whatever the type-level verdict was.
    was_unverified = finding.outcome == UNKNOWN
    for prop_rule in _property_rules(comp.type_name, source, target):
        if prop_rule.old_name in comp.properties:
            finding.property_changes.append(
                {
                    "property": prop_rule.old_name,
                    "from": prop_rule.old_name,
                    "to": prop_rule.new_name or "(removed)",
                    "reason": prop_rule.explanation or "changed in target version",
                }
            )
            if finding.outcome in (COMPATIBLE, UNKNOWN):
                finding.outcome = CONFIG_CHANGE
                finding.decided_by = "rules"

    # 3. Structural checks that no per-type rule would catch.
    if comp.scheduling_strategy == "EVENT_DRIVEN" and not target.supports_event_driven:
        finding.property_changes.append(
            {
                "property": "schedulingStrategy",
                "from": "EVENT_DRIVEN",
                "to": "TIMER_DRIVEN",
                "reason": f"EVENT_DRIVEN scheduling was removed in NiFi {target.version.split('.')[0]}.0",
            }
        )
        if finding.outcome in (COMPATIBLE, UNKNOWN):
            finding.outcome = CONFIG_CHANGE
            finding.decided_by = "structural"

    # A component that started out unverified but picked up known changes should
    # lead with those changes; the "we could not verify the type" caveat is a
    # footnote, not the headline it would otherwise become in the report.
    if was_unverified and finding.outcome == CONFIG_CHANGE:
        changes = ", ".join(
            f"{c['from']} → {c['to']}" for c in finding.property_changes
        )
        finding.explanation = (
            f"Requires known changes for NiFi {target.version}: {changes}. They are suggested "
            f"and applied only if you accept them. Note that {comp.short_type} itself was not "
            "verified by a curated rule."
        )
        finding.recommendation = (
            f"Accept the suggested changes above. Separately, confirm {comp.short_type} "
            f"is installed on NiFi {target.version}."
        )
    elif not finding.explanation and finding.property_changes:
        finding.explanation = (
            f"Requires configuration changes for NiFi {target.version}; they are applied "
            "only if you accept them."
        )

    if finding.outcome in NEEDS_REVIEW:
        finding.manual_review = True
    finding.suggestions = _suggestions_for(comp, finding, life, source, target)
    return finding


def _analyze_service(
    comp: Component,
    source: Any,
    target: Any,
) -> ComponentFinding:
    finding = ComponentFinding(
        identifier=comp.identifier,
        name=comp.name or comp.short_type,
        kind="controllerService",
        source_type=comp.type_name,
        group_path=comp.group_path,
        outcome=COMPATIBLE,
    )

    rule = service_rule_for(comp.type_name, target.version)
    life = lifecycle_verdict(comp.type_name, source.version, target.version)

    if rule is not None:
        finding.outcome = rule.outcome
        finding.explanation = rule.explanation
        finding.target_type = rule.replacement
        finding.manual_review = rule.manual_review
        finding.block_auto_migration = rule.manual_review or rule.outcome == REMOVED
        finding.decided_by = "rules"
    elif life is not None:
        _apply_lifecycle(finding, life)
    else:
        finding.outcome = UNKNOWN
        finding.decided_by = "default"
        finding.explanation = (
            f"No rule covers controller service {comp.short_type}. Confirm it exists on "
            f"NiFi {target.version}."
        )
    _recommend_swap(finding, life)

    for prop_rule in _property_rules(comp.type_name, source, target):
        if prop_rule.old_name in comp.properties:
            finding.property_changes.append(
                {
                    "property": prop_rule.old_name,
                    "from": prop_rule.old_name,
                    "to": prop_rule.new_name or "(removed)",
                    "reason": prop_rule.explanation or "changed in target version",
                }
            )
            if finding.outcome in (COMPATIBLE, UNKNOWN):
                finding.outcome = CONFIG_CHANGE

    if finding.outcome in NEEDS_REVIEW:
        finding.manual_review = True
    finding.suggestions = _suggestions_for(comp, finding, life, source, target)
    return finding


def _property_rules(type_name: str, source: Any, target: Any) -> list[Any]:
    return [
        *property_rules_for(type_name, target.version),
        *downgrade_property_rules_for(type_name, source.version, target.version),
    ]


def _apply_lifecycle(finding: ComponentFinding, life: LifecycleVerdict) -> None:
    """Verdict for a type the component history knows but no curated rule covers."""
    finding.decided_by = "rules"
    finding.explanation = life.explanation
    if life.status == "available":
        finding.outcome = COMPATIBLE
    elif life.status == "deprecated":
        finding.outcome = DEPRECATED
    else:
        finding.outcome = REMOVED
        finding.manual_review = True
        finding.block_auto_migration = True


def _recommend_swap(finding: ComponentFinding, life: LifecycleVerdict | None) -> None:
    if life is None or life.status == "available":
        return
    if life.swap_to:
        short = _short(life.swap_to)
        finding.target_type = short
        finding.recommendation = (
            f"Replace with {short} (suggested; accept it to apply)."
            if life.required
            else f"Optionally replace with {short}; it is still available, so this is your choice."
        )
        if life.swap is not None:
            for prop in life.swap.new_required_properties:
                if prop not in finding.new_required_properties:
                    finding.new_required_properties.append(prop)
    elif life.status == "not_available" and not finding.recommendation:
        finding.recommendation = (
            "No equivalent exists on the target release. Rebuild this component by hand "
            "there, or remove it."
        )


def suggestion_id(component_id: str, kind: str, source: str) -> str:
    return f"{component_id}:{kind}:{source}"


def _suggestions_for(
    comp: Component,
    finding: ComponentFinding,
    life: LifecycleVerdict | None,
    source: Any,
    target: Any,
) -> list[dict[str, Any]]:
    direction = "downgrade" if is_downgrade(source.version, target.version) else "upgrade"
    base = {
        "componentId": finding.identifier,
        "component": finding.name,
        "componentKind": finding.kind,
        "groupPath": finding.group_path,
        "direction": direction,
    }
    out: list[dict[str, Any]] = []

    if life is not None and life.swap_to:
        swap = life.swap or Swap()
        props = comp.properties or {}

        def on_target(name: str) -> str:
            return property_name_on(life.swap_to, name, target.version)

        mapped = {old: on_target(new) for old, new in swap.property_map.items() if old in props}
        dropped = (
            []
            if swap.carry_other_properties
            else [k for k in props if k not in swap.property_map]
        )
        reason = life.explanation + (f" {swap.note}" if swap.note else "")
        out.append(
            {
                **base,
                "id": suggestion_id(finding.identifier, "type", comp.short_type),
                "kind": "type",
                "from": comp.type_name,
                "to": life.swap_to,
                "reason": reason,
                "risk": swap.risk,
                "required": life.required,
                "details": {
                    "bundleArtifact": swap.bundle_artifact,
                    "propertyMap": mapped,
                    "setProperties": {on_target(k): v for k, v in swap.set_properties.items()},
                    "valueMap": {k: dict(v) for k, v in swap.value_map.items()},
                    "relationshipMap": dict(swap.relationship_map),
                    "carryOtherProperties": swap.carry_other_properties,
                    "dropsProperties": dropped,
                    "newRequiredProperties": [on_target(p) for p in swap.new_required_properties],
                    "note": swap.note,
                },
            }
        )

    for change in finding.property_changes:
        if change.get("property") == "schedulingStrategy":
            kind = "schedulingStrategy"
        elif change.get("to") == "(removed)":
            kind = "property-removed"
        else:
            kind = "property"
        out.append(
            {
                **base,
                "id": suggestion_id(finding.identifier, kind, str(change.get("from"))),
                "kind": kind,
                "from": change.get("from"),
                "to": None if kind == "property-removed" else change.get("to"),
                "reason": change.get("reason", ""),
                "risk": "safe",
                # The target does not understand the old value, so skipping
                # this leaves the component invalid there.
                "required": True,
            }
        )
    return out


def _check_variables(doc: FlowDocument, source: Any, target: Any, analysis: MigrationAnalysis) -> None:
    """Warn about ${var} references that break when the Variable Registry is gone."""
    if target.supports_variable_registry or not doc.variables:
        return
    names = {str(v.get("name")) for v in doc.variables if v.get("name")}
    if not names:
        return

    affected: list[str] = []
    for comp in doc.components:
        for key, value in (comp.properties or {}).items():
            if not isinstance(value, str):
                continue
            for match in _BARE_VAR.findall(value):
                if match in names:
                    affected.append(f"{comp.name or comp.short_type}.{key} → ${{{match}}}")

    analysis.warnings.append(
        f"The flow declares {len(names)} Variable Registry variable(s) "
        f"({', '.join(sorted(names)[:8])}{'…' if len(names) > 8 else ''}), which NiFi "
        f"{target.version.split('.')[0]}.0 removed. "
        + (
            f"{len(affected)} property reference(s) will stop resolving: "
            f"{'; '.join(affected[:6])}{'…' if len(affected) > 6 else ''}. "
            if affected
            else ""
        )
        + "Recreate them as Parameter Context parameters and change ${name} to #{name}. "
        "This tool does not rewrite them automatically because ${...} is also valid "
        "attribute-expression syntax."
    )


def _check_parameter_contexts(
    doc: FlowDocument, source: Any, target: Any, analysis: MigrationAnalysis
) -> None:
    if doc.parameter_contexts and not target.supports_parameter_contexts:
        analysis.warnings.append(
            f"The flow uses Parameter Contexts, which NiFi {target.version} predates "
            "(they arrived in 1.10). The migrated flow keeps them, but the target will ignore them."
        )


# --- Generation ---------------------------------------------------------------


@dataclass
class MigrationResult:
    """The generated flow plus a record of exactly what was and was not changed."""

    migrated: Any
    output_format: str
    xml_text: str = ""
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    manual_markers: list[dict[str, Any]] = field(default_factory=list)
    dialect_notes: list[str] = field(default_factory=list)
    #: Suggestion ids the user accepted that were applied.
    accepted: list[str] = field(default_factory=list)
    #: Suggestions the user did not accept, left unapplied.
    declined: list[dict[str, Any]] = field(default_factory=list)
    #: Accepted ids that matched no suggestion in this analysis.
    unknown_accepted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Component id → {old relationship: new relationship} for accepted type swaps.
    relationship_maps: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputFormat": self.output_format,
            "appliedChanges": self.applied_changes,
            "skipped": self.skipped,
            "manualMarkers": self.manual_markers,
            "dialectNotes": self.dialect_notes,
            "acceptedSuggestions": self.accepted,
            "declinedSuggestions": self.declined,
            "unknownAccepted": self.unknown_accepted,
            "warnings": self.warnings,
            "appliedCount": len(self.applied_changes),
            "skippedCount": len(self.skipped),
            "manualCount": len(self.manual_markers),
            "acceptedCount": len(self.accepted),
            "declinedCount": len(self.declined),
            "hasXml": bool(self.xml_text),
        }


def generate_migrated_flow(
    doc: FlowDocument,
    analysis: MigrationAnalysis,
    output_format: str = FORMAT_JSON_SNAPSHOT,
    accepted: Iterable[str] | None = None,
) -> MigrationResult:
    """Produce a migrated copy of the flow.

    Rules of engagement:
      * The uploaded document is never mutated — everything happens on a deep copy.
      * A component change (type swap, property rename or removal, scheduling)
        is applied only when its suggestion id is in `accepted`. Nothing is
        accepted by default.
      * Format changes always happen: XML-to-JSON conversion, the NAR bundle
        version and the target's JSON dialect.
      * A component needing review is copied through **unchanged** and annotated,
        unless an accepted replacement resolves it.
      * XML templates cannot target 2.x (templates were removed).
    """
    requested = (output_format or FORMAT_JSON_SNAPSHOT).strip()
    if requested not in {FORMAT_JSON_SNAPSHOT, FORMAT_XML_TEMPLATE}:
        raise ValueError(
            f"Unsupported output format {requested!r}. Use {FORMAT_JSON_SNAPSHOT} or {FORMAT_XML_TEMPLATE}."
        )

    target = get_version(analysis.target_version)
    if requested == FORMAT_XML_TEMPLATE and target is not None and not target.supports_templates:
        raise ValueError(
            f"NiFi {analysis.target_version} cannot import XML templates "
            "(removed in 2.0). Choose JSON output or a 1.x target."
        )

    by_id = {f.identifier: f for f in analysis.findings}
    chosen = {str(a) for a in (accepted or ())}

    if doc.source_format == FORMAT_XML_TEMPLATE:
        result = _generate_from_xml(doc, analysis, by_id, chosen)
    else:
        result = _generate_from_json(doc, analysis, by_id, chosen)

    known = {s["id"] for s in analysis.suggestions()}
    result.unknown_accepted = sorted(chosen - known)
    required = [d for d in result.declined if d.get("required")]
    if required:
        def describe(d: dict[str, Any]) -> str:
            if d["kind"] == "type":
                return f"{d['component']}: {_short(d['from'])} → {_short(d['to'])}"
            return f"{d['component']}: {d['from']} → {d['to'] or 'removed'}"

        listed = "; ".join(describe(d) for d in required[:6])
        result.warnings.append(
            f"{len(required)} required change(s) were not accepted, so NiFi "
            f"{analysis.target_version} may reject or invalidate those components: {listed}"
            f"{'…' if len(required) > 6 else ''}."
        )

    if isinstance(result.migrated, dict):
        _remap_relationships(result.migrated, result)
        _stamp_provenance(result.migrated, analysis)
        result.dialect_notes = apply_json_dialect(result.migrated, analysis.target_version)
        for note in result.dialect_notes:
            if note not in analysis.warnings:
                analysis.warnings.append(note)

    if requested == FORMAT_XML_TEMPLATE:
        from versioned_flow import template_from_snapshot

        result.xml_text = template_from_snapshot(
            result.migrated, analysis.target_version
        )
        result.output_format = FORMAT_XML_TEMPLATE
    else:
        result.output_format = FORMAT_JSON_SNAPSHOT
    return result


def _generate_from_json(
    doc: FlowDocument,
    analysis: MigrationAnalysis,
    by_id: dict[str, ComponentFinding],
    accepted: set[str],
) -> MigrationResult:
    migrated = copy.deepcopy(doc.original)
    result = MigrationResult(migrated=migrated, output_format=FORMAT_JSON_SNAPSHOT)
    target = get_version(analysis.target_version)

    def visit(node: dict[str, Any]) -> None:
        for proc in node.get("processors") or []:
            _apply_to_node(proc, by_id, result, target, accepted)
        for svc in node.get("controllerServices") or []:
            _apply_to_node(svc, by_id, result, target, accepted)
        # 2.x has no Variable Registry: drop the (now meaningless) block, but
        # record it so the report can say the values were carried nowhere.
        if target is not None and not target.supports_variable_registry:
            variables = node.get("variables")
            if isinstance(variables, dict) and variables:
                result.manual_markers.append(
                    {
                        "component": node.get("name") or "process group",
                        "reason": "variable_registry_removed",
                        "detail": (
                            f"Removed {len(variables)} group variable(s) — the Variable Registry "
                            "does not exist on the target. Recreate them as Parameter Context "
                            "parameters: " + ", ".join(sorted(variables)[:10])
                        ),
                    }
                )
                node["variables"] = {}
        for child in node.get("processGroups") or []:
            visit(child)

    root = None
    for key in ("flowContents", "rootGroup"):
        if isinstance(migrated.get(key), dict):
            root = migrated[key]
            break
    if root is None and isinstance(migrated, dict):
        root = migrated
    if isinstance(root, dict):
        visit(root)

    return result


def _apply_to_node(
    node: dict[str, Any],
    by_id: dict[str, ComponentFinding],
    result: MigrationResult,
    target: Any,
    accepted: set[str],
) -> None:
    identifier = str(node.get("identifier") or node.get("id") or "")
    finding = by_id.get(identifier)
    if finding is None:
        return

    label = node.get("name") or finding.name

    chosen: list[dict[str, Any]] = []
    for suggestion in finding.suggestions:
        if suggestion["id"] in accepted:
            chosen.append(suggestion)
        else:
            result.declined.append(
                {
                    "id": suggestion["id"],
                    "component": label,
                    "identifier": identifier,
                    "kind": suggestion["kind"],
                    "from": suggestion["from"],
                    "to": suggestion["to"],
                    "required": suggestion["required"],
                    "direction": suggestion["direction"],
                    "reason": suggestion["reason"],
                }
            )

    swapped = False
    for suggestion in chosen:
        if suggestion["kind"] == "type":
            _apply_type_swap(node, suggestion, result, label, identifier)
            swapped = True
    for suggestion in chosen:
        if suggestion["kind"] != "type":
            _apply_simple_suggestion(node, suggestion, result, label, identifier)
    result.accepted.extend(s["id"] for s in chosen)

    # Never rewrite something that has no trustworthy mechanical fix. Mark it.
    needs_review = finding.block_auto_migration and not swapped
    comments = str(node.get("comments") or "")
    if not needs_review and "[MANUAL REVIEW REQUIRED" in comments:
        # Left by an earlier Flowgenix run against a target that needed review.
        node["comments"] = _without_banner(comments)
        _record(
            result, label, identifier, "comments", "manual-review note", None,
            f"No manual review needed on NiFi {target.version if target else 'the target'}; removed the note an "
            "earlier Flowgenix migration left.",
        )
    if needs_review:
        result.manual_markers.append(
            {
                "component": label,
                "identifier": identifier,
                "outcome": finding.outcome,
                "sourceType": finding.source_type,
                "suggestedType": finding.target_type,
                "reason": finding.explanation,
                "recommendation": finding.recommendation,
            }
        )
        comments = _without_banner(comments)
        banner = (
            f"[MANUAL REVIEW REQUIRED — {finding.outcome}] {finding.explanation} "
            f"{finding.recommendation}".strip()
        )
        node["comments"] = f"{banner}\n\n{comments}".strip()
        result.skipped.append(
            {
                "component": label,
                "identifier": identifier,
                "reason": "needs manual review; left unchanged deliberately",
                "outcome": finding.outcome,
            }
        )
        return

    # A component the rule base does not cover is almost always fine. Saying so
    # in the artifact would rewrite the comments of every ordinary processor, so
    # the "not verified" list stays in the report where it does not alter the flow.

    # Re-stamp the NAR bundle so the target loads the right version.
    bundle = node.get("bundle")
    if (
        isinstance(bundle, dict)
        and target is not None
        and bundle.get("version")
        and follows_nifi_release(bundle)
    ):
        old_version = bundle.get("version")
        if old_version != target.version:
            bundle["version"] = target.version
            result.applied_changes.append(
                {
                    "component": label,
                    "identifier": identifier,
                    "change": "bundleVersion",
                    "from": old_version,
                    "to": target.version,
                    "reason": "NAR bundle version follows the NiFi release",
                }
            )


def _without_banner(comments: str) -> str:
    return "\n\n".join(
        p for p in comments.split("\n\n") if not p.strip().startswith("[MANUAL REVIEW REQUIRED")
    ).strip()


def _record(
    result: MigrationResult,
    label: str,
    identifier: str,
    change: str,
    old: Any,
    new: Any,
    reason: str,
    suggestion: str | None = None,
) -> None:
    entry = {
        "component": label,
        "identifier": identifier,
        "change": change,
        "from": old,
        "to": new,
        "reason": reason,
    }
    if suggestion:
        entry["suggestion"] = suggestion
    result.applied_changes.append(entry)


def _apply_simple_suggestion(
    node: dict[str, Any],
    suggestion: dict[str, Any],
    result: MigrationResult,
    label: str,
    identifier: str,
) -> None:
    kind, old, new = suggestion["kind"], suggestion["from"], suggestion["to"]
    if kind == "schedulingStrategy":
        node["schedulingStrategy"] = new
        _record(result, label, identifier, kind, old, new, suggestion["reason"], suggestion["id"])
        return
    props = node.get("properties")
    if not isinstance(props, dict) or old not in props:
        return
    descriptors = node.get("propertyDescriptors")
    if kind == "property" and new:
        props[new] = props.pop(old)
        if isinstance(descriptors, dict) and old in descriptors:
            descriptor = dict(descriptors.pop(old) or {})
            descriptor.update({"name": new, "displayName": new})
            descriptors[new] = descriptor
        _record(result, label, identifier, "property", old, new, suggestion["reason"], suggestion["id"])
    elif kind == "property-removed":
        props.pop(old)
        if isinstance(descriptors, dict):
            descriptors.pop(old, None)
        _record(result, label, identifier, "property-removed", old, None, suggestion["reason"], suggestion["id"])


def _apply_type_swap(
    node: dict[str, Any],
    suggestion: dict[str, Any],
    result: MigrationResult,
    label: str,
    identifier: str,
) -> None:
    """Replace the component's type and carry its configuration across."""
    details = suggestion.get("details") or {}
    sid = suggestion["id"]
    old_type = str(node.get("type") or suggestion["from"])
    new_type = suggestion["to"]
    node["type"] = new_type
    bundle = node.get("bundle")
    if details.get("bundleArtifact") and isinstance(bundle, dict):
        bundle["artifact"] = details["bundleArtifact"]
        bundle["group"] = NIFI_BUNDLE_GROUP
    _record(result, label, identifier, "type", old_type, new_type, suggestion["reason"], sid)

    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    descriptors = node.get("propertyDescriptors")
    property_map: dict[str, str] = details.get("propertyMap") or {}
    value_map: dict[str, dict[str, str]] = details.get("valueMap") or {}
    carry = bool(details.get("carryOtherProperties"))
    why = f"{_short(new_type)} has no equivalent property"

    new_props: dict[str, Any] = {}
    new_descriptors: dict[str, Any] = {}
    dropped: list[str] = []
    for old, value in props.items():
        new = property_map.get(old) or (old if carry else None)
        if new is None:
            dropped.append(old)
            _record(result, label, identifier, "property-removed", old, None, why, sid)
            continue
        mapped = value_map.get(old, {}).get(value, value) if isinstance(value, str) else value
        new_props[new] = mapped
        if new != old:
            _record(result, label, identifier, "property", old, new, f"renamed for {_short(new_type)}", sid)
        if mapped != value:
            _record(result, label, identifier, "property-value", f"{new}={value}", f"{new}={mapped}",
                    f"{_short(new_type)} spells this value differently", sid)
        if isinstance(descriptors, dict) and old in descriptors:
            descriptor = dict(descriptors[old] or {})
            if new != old:
                descriptor.update({"name": new, "displayName": new})
            new_descriptors[new] = descriptor
    for key, value in (details.get("setProperties") or {}).items():
        new_props[key] = value
        _record(result, label, identifier, "property-set", key, value,
                f"{_short(new_type)} needs this to behave like {_short(old_type)}", sid)
    node["properties"] = new_props
    if isinstance(descriptors, dict):
        node["propertyDescriptors"] = new_descriptors

    rel_map: dict[str, str] = details.get("relationshipMap") or {}
    if rel_map:
        for key in ("autoTerminatedRelationships", "retriedRelationships"):
            if isinstance(node.get(key), list):
                node[key] = [rel_map.get(r, r) for r in node[key]]
        result.relationship_maps[identifier] = rel_map

    parts = [f"[Flowgenix] Replaced {_short(old_type)} with {_short(new_type)} (accepted suggestion)."]
    if dropped:
        parts.append("Not carried over: " + ", ".join(dropped) + ".")
    if details.get("newRequiredProperties"):
        parts.append("Configure before starting: " + ", ".join(details["newRequiredProperties"]) + ".")
    if details.get("note"):
        parts.append(str(details["note"]))
    comments = str(node.get("comments") or "")
    node["comments"] = f"{' '.join(parts)}\n\n{comments}".strip()


def _remap_relationships(migrated: dict[str, Any], result: MigrationResult) -> None:
    """Rename connection relationships leaving a component whose type was swapped."""
    if not result.relationship_maps:
        return
    root = migrated.get("flowContents") or migrated.get("rootGroup") or migrated

    def visit(group: dict[str, Any]) -> None:
        for conn in group.get("connections") or []:
            source = conn.get("source") or {}
            rel_map = result.relationship_maps.get(str(source.get("id") or ""))
            before = conn.get("selectedRelationships")
            if not rel_map or not isinstance(before, list):
                continue
            after = [rel_map.get(r, r) for r in before]
            if after != before:
                conn["selectedRelationships"] = after
                _record(
                    result,
                    conn.get("name") or f"{source.get('name') or 'connection'} connection",
                    str(conn.get("identifier") or ""),
                    "relationship",
                    ", ".join(before),
                    ", ".join(after),
                    f"{source.get('name') or 'The source'} was replaced; its relationships have new names.",
                )
        for child in group.get("processGroups") or []:
            visit(child)

    if isinstance(root, dict):
        visit(root)


def _generate_from_xml(
    doc: FlowDocument,
    analysis: MigrationAnalysis,
    by_id: dict[str, ComponentFinding],
    accepted: set[str],
) -> MigrationResult:
    """Convert a 1.x XML template into a NiFi flow definition for the target.

    NiFi 2.0 removed templates, so emitting XML for a 2.x target would produce a
    file the target cannot import. Even for a 1.x target we emit JSON, because
    that is the format both lines consume.

    The conversion is delegated to `versioned_flow`, which reproduces NiFi's
    `VersionedProcessGroup` model exactly — including the group hierarchy and the
    connection endpoints. Approximating that shape is what makes NiFi reject the
    import with a bare "An unexpected error has occurred."
    """
    from versioned_flow import snapshot_from_xml_template

    target = get_version(analysis.target_version)
    result = MigrationResult(migrated=None, output_format=FORMAT_JSON_SNAPSHOT)

    if doc.xml_root is None:
        raise ValueError("Template was parsed without retaining its XML tree; cannot convert it.")

    def apply(kind: str, identifier: str, node: dict[str, Any]) -> None:
        _apply_to_node(node, by_id, result, target, accepted)

    migrated = snapshot_from_xml_template(
        doc.xml_root,
        target.version if target else analysis.target_version,
        apply=apply,
    )

    if doc.variables:
        names = sorted({str(v["name"]) for v in doc.variables if v.get("name")})
        result.manual_markers.append(
            {
                "component": "process group variables",
                "reason": "variable_registry_removed"
                if target is not None and not target.supports_variable_registry
                else "variables_carried",
                "detail": (
                    f"The template declared {len(names)} variable(s): "
                    + ", ".join(names[:10])
                    + ". Recreate them as Parameter Context parameters on the target."
                ),
            }
        )

    result.migrated = migrated
    return result


def _stamp_provenance(migrated: Any, analysis: MigrationAnalysis) -> None:
    """Record where the migrated artifact came from, inside the artifact itself.

    Written into the root group's `comments` rather than as a top-level key.
    NiFi deserializes a flow definition into its `VersionedFlowSnapshot` model
    and rejects the entire file when it meets a field it does not recognise.
    """
    if not isinstance(migrated, dict):
        return

    notes = [
        f"[Flowgenix migration] NiFi {analysis.source_version} → {analysis.target_version}, "
        f"from {analysis.filename} ({analysis.source_format})."
    ]
    if analysis.source_format == FORMAT_XML_TEMPLATE:
        notes.append(
            f"Migrated from a NiFi {analysis.source_version} XML template to a NiFi "
            f"{analysis.target_version} flow definition by Flowgenix "
            f"(source file: {analysis.filename}). NiFi 2.x cannot import XML templates, so "
            "the template's contents were converted to this flow definition. Components are "
            "imported stopped — review them before starting the flow."
        )
    root = None
    for key in ("flowContents", "rootGroup"):
        if isinstance(migrated.get(key), dict):
            root = migrated[key]
            break
    if root is None:
        return

    # A file Flowgenix already migrated carries the previous run's notes; they
    # describe a migration that this one supersedes.
    kept = [
        p
        for p in str(root.get("comments") or "").split("\n\n")
        if p.strip() and not _is_flowgenix_note(p)
    ]
    root["comments"] = "\n\n".join(notes + kept).strip()


def _is_flowgenix_note(paragraph: str) -> bool:
    text = paragraph.strip()
    return text.startswith("[Flowgenix migration]") or (
        text.startswith("Migrated from a NiFi ") and "by Flowgenix" in text
    )


def migrated_json_text(result: MigrationResult) -> str:
    return json.dumps(result.migrated, indent=2, ensure_ascii=False)


def migrated_xml_text(result: MigrationResult) -> str:
    return result.xml_text


def build_report(
    analysis: MigrationAnalysis,
    generation: Any | None = None,
) -> dict[str, Any]:
    """The machine-readable Migration Report (downloaded as JSON)."""
    summary = analysis.summary()
    report: dict[str, Any] = {
        "report": "NiFi Migration Report",
        "generatedBy": "Flowgenix",
        "sourceVersion": analysis.source_version,
        "targetVersion": analysis.target_version,
        "sourceFile": analysis.filename,
        "sourceFormat": analysis.source_format,
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
        "suggestions": analysis.suggestions(),
        "manualInterventionRequired": [f.to_dict() for f in analysis.findings_needing_review()],
        "flowLevelConcerns": analysis.concerns,
        "warnings": analysis.warnings,
        "errors": analysis.errors,
    }
    if generation is not None:
        report["generation"] = generation.to_dict() if hasattr(generation, "to_dict") else generation
    return report


def render_report_markdown(report: dict[str, Any]) -> str:
    """Human-readable Migration Report."""
    s = report.get("summary") or {}
    lines: list[str] = [
        "# NiFi Migration Report",
        "",
        f"- **Source version:** {report.get('sourceVersion')}",
        f"- **Target version:** {report.get('targetVersion')}",
        f"- **Source file:** `{report.get('sourceFile')}`",
        f"- **File format:** {report.get('sourceFormat')}",
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
            "These components were **not** modified in the generated flow.",
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
            "| Component | Current type | Changes required | Recommended migration | Manual review |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in list(replaced) + list(changed):
            changes = "; ".join(
                f"{c.get('from')} → {c.get('to')}" for c in (f.get("propertyChanges") or [])
            ) or _cell(f.get("explanation"))
            lines.append(
                f"| {f.get('name')} | `{_short(f.get('sourceType'))}` "
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
            "No migration rule covers these types. They are most likely unchanged, "
            "but this run did not prove it.",
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
            f"- Suggestions accepted: {generation.get('acceptedCount', 0)}",
            f"- Suggestions not accepted: {generation.get('declinedCount', 0)}",
            f"- Changes applied: {generation.get('appliedCount', 0)}",
            f"- Left unchanged for review: {generation.get('skippedCount', 0)}",
            f"- Manual-review markers written into the flow: {generation.get('manualCount', 0)}",
            "",
        ]
        for warning in generation.get("warnings") or []:
            lines += [f"> {warning}", ""]
        applied = generation.get("appliedChanges") or []
        if applied:
            lines += ["| Component | Change | From | To |", "| --- | --- | --- | --- |"]
            for c in applied:
                lines.append(
                    f"| {c.get('component')} | {c.get('change')} | {_cell(c.get('from'))} | {_cell(c.get('to'))} |"
                )
            lines.append("")
        declined = generation.get("declinedSuggestions") or []
        if declined:
            lines += [
                "### Suggestions not accepted",
                "",
                "These were left exactly as they were in the source flow.",
                "",
                "| Component | Change | From | To | Required |",
                "| --- | --- | --- | --- | --- |",
            ]
            for d in declined:
                shorten = _short if d.get("kind") == "type" else (lambda v: v or "")
                lines.append(
                    f"| {d.get('component')} | {d.get('kind')} | {_cell(shorten(d.get('from')))} "
                    f"| {_cell(shorten(d.get('to'))) or 'removed'} | {'Yes' if d.get('required') else 'No'} |"
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
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()

