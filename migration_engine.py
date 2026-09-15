"""Analyse a parsed flow against a target NiFi version, and generate a migrated copy.

Compatibility is decided by a strict precedence, so the answer is reproducible
and never depends on a language model's mood:

    1. **Live catalog** at the target version, when the caller supplies one.
       If the target instance reports the processor type as installed, it is
       installed; if it does not, it is not. Nothing overrides this.
    2. **Curated rules** in `migration_rules.py` — known renames, removals,
       replacements, and property changes, each carrying an explanation.
    3. **Structural checks** — EVENT_DRIVEN on a 2.x target, `${var}` references
       when the Variable Registry is gone, and so on.
    4. Anything still unresolved is reported as `unknown` and flagged for review.
       `migration_ai` can then annotate those entries, but its output is labelled
       as a suggestion and never silently applied to the generated flow.

The generator honours that last point literally: a component whose outcome needs
review is copied through **unchanged** and marked, rather than being rewritten on
a guess. The uploaded file itself is never mutated — generation always works on a
deep copy.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from flow_document import (
    FORMAT_JSON_SNAPSHOT,
    FORMAT_STUDIO_SPEC,
    FORMAT_XML_TEMPLATE,
    Component,
    FlowDocument,
)
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
    flow_concerns,
    processor_rule_for,
    property_rules_for,
    service_rule_for,
)
from nifi_catalog import parse_version
from nifi_versions import get_version, is_downgrade, is_major_upgrade, resolve_or_raise

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
    decided_by: str = "rules"  # catalog | rules | structural | ai | default
    ai_suggestion: str = ""

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
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
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
    target_catalog: Any | None = None,
) -> MigrationAnalysis:
    """Classify every component in `doc` against `target_version`.

    `target_catalog` is an optional live `NiFiCatalog` whose version matches the
    target; when present it is treated as ground truth for type availability.
    """
    source = resolve_or_raise(source_version, "source")
    target = resolve_or_raise(target_version, "target")

    analysis = MigrationAnalysis(
        source_version=source.version,
        target_version=target.version,
        source_format=doc.source_format,
        filename=doc.filename,
        document_summary=doc.summary(),
    )

    catalog_usable = _catalog_matches_target(target_catalog, target.version, analysis)
    if catalog_usable:
        analysis.catalog_version = getattr(target_catalog, "version", None)

    if is_downgrade(source.version, target.version):
        analysis.warnings.append(
            f"Target {target.version} is older than source {source.version}. Downgrades are "
            "not a supported NiFi path: components added after the target release will "
            "have no equivalent. Review every finding carefully."
        )

    for comp in doc.components:
        if comp.kind == "processor":
            analysis.findings.append(
                _analyze_processor(comp, source, target, target_catalog if catalog_usable else None)
            )
        elif comp.kind == "controllerService":
            analysis.findings.append(
                _analyze_service(comp, source, target, target_catalog if catalog_usable else None)
            )
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


def _catalog_matches_target(catalog: Any, target_version: str, analysis: MigrationAnalysis) -> bool:
    """Only trust a live catalog when it really is the target release."""
    if catalog is None:
        return False
    version = getattr(catalog, "version", None)
    if not version:
        return False
    if parse_version(version)[:2] != parse_version(target_version)[:2]:
        analysis.warnings.append(
            f"Connected NiFi is {version} but the migration target is {target_version}; "
            "the live catalog was ignored and the analysis used the built-in rule base only. "
            "Point Flow Studio at a NiFi running the target version for the most accurate result."
        )
        return False
    return True


def _analyze_processor(
    comp: Component,
    source: Any,
    target: Any,
    catalog: Any | None,
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
    installed: bool | None = None
    if catalog is not None:
        installed = catalog.has_processor(comp.type_name)

    # 1. Curated rule wins for *explanation*; the catalog wins for *existence*.
    if rule is not None:
        finding.outcome = rule.outcome
        finding.explanation = rule.explanation
        finding.target_type = rule.replacement
        finding.manual_review = rule.manual_review
        # A rule flagged for review, or one with no valid target at all, means
        # there is no mechanical rewrite to trust.
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
    elif installed is False:
        # Catalog is authoritative: the type genuinely is not on the target.
        finding.outcome = REMOVED
        finding.manual_review = True
        finding.block_auto_migration = True
        finding.decided_by = "catalog"
        finding.explanation = (
            f"{comp.short_type} is not installed on the connected NiFi {getattr(catalog, 'version', target.version)}. "
            "It was either removed in this release or ships in a NAR that is not deployed."
        )
        finding.recommendation = (
            "Confirm whether the NAR is simply missing (install it) or the processor was "
            "removed (pick a replacement)."
        )
    elif installed is True:
        finding.outcome = COMPATIBLE
        finding.decided_by = "catalog"
        finding.explanation = (
            f"{comp.short_type} is installed on the connected NiFi "
            f"{getattr(catalog, 'version', target.version)}."
        )
    else:
        # No catalog and no rule: honest "not verified" rather than a false pass.
        # This neither blocks generation nor counts as manual review — it gets its
        # own "not verified" section in the report.
        finding.outcome = UNKNOWN
        finding.decided_by = "default"
        finding.explanation = (
            f"No migration rule covers {comp.short_type}, and no NiFi at the target version "
            "was connected to confirm it is installed. It is most likely unchanged, but this "
            "has not been verified."
        )
        finding.recommendation = (
            f"Confirm {comp.short_type} exists on NiFi {target.version}, or connect Flow Studio "
            "to a target-version instance and re-run the analysis."
        )

    # 2. Property renames apply on top of whatever the type-level verdict was.
    was_unverified = finding.outcome == UNKNOWN
    for prop_rule in property_rules_for(comp.type_name, target.version):
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
            f"Requires known changes for NiFi {target.version}: {changes}. The migrated flow "
            f"applies them. Note that {comp.short_type} itself was not verified against a live "
            "target instance."
        )
        finding.recommendation = (
            f"The changes above are applied automatically. Separately, confirm {comp.short_type} "
            f"is installed on NiFi {target.version}."
        )
    elif not finding.explanation and finding.property_changes:
        finding.explanation = (
            f"Requires configuration changes for NiFi {target.version}, applied automatically."
        )

    if finding.outcome in NEEDS_REVIEW:
        finding.manual_review = True
    return finding


def _analyze_service(
    comp: Component,
    source: Any,
    target: Any,
    catalog: Any | None,
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
    installed = catalog.has_service(comp.type_name) if catalog is not None else None

    if rule is not None:
        finding.outcome = rule.outcome
        finding.explanation = rule.explanation
        finding.target_type = rule.replacement
        finding.manual_review = rule.manual_review
        finding.block_auto_migration = rule.manual_review or rule.outcome == REMOVED
        finding.decided_by = "rules"
    elif installed is False:
        finding.outcome = REMOVED
        finding.manual_review = True
        finding.block_auto_migration = True
        finding.decided_by = "catalog"
        finding.explanation = (
            f"Controller service {comp.short_type} is not installed on the connected NiFi "
            f"{getattr(catalog, 'version', target.version)}."
        )
    elif installed is True:
        finding.outcome = COMPATIBLE
        finding.decided_by = "catalog"
        finding.explanation = f"{comp.short_type} is installed on the connected target NiFi."
    else:
        finding.outcome = UNKNOWN
        finding.decided_by = "default"
        finding.explanation = (
            f"No rule covers controller service {comp.short_type} and no target-version NiFi "
            "was connected to confirm availability."
        )

    for prop_rule in property_rules_for(comp.type_name, target.version):
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
    return finding


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
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    manual_markers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputFormat": self.output_format,
            "appliedChanges": self.applied_changes,
            "skipped": self.skipped,
            "manualMarkers": self.manual_markers,
            "appliedCount": len(self.applied_changes),
            "skippedCount": len(self.skipped),
            "manualCount": len(self.manual_markers),
        }


def generate_migrated_flow(
    doc: FlowDocument,
    analysis: MigrationAnalysis,
) -> MigrationResult:
    """Produce a migrated copy of the flow.

    Rules of engagement:
      * The uploaded document is never mutated — everything happens on a deep copy.
      * Only changes backed by the catalog or the curated rule base are applied.
      * A component needing review is copied through **unchanged** and annotated
        so it is impossible to mistake it for something the tool fixed.
      * XML templates cannot target 2.x (templates were removed), so those are
        converted to a JSON flow definition.
    """
    by_id = {f.identifier: f for f in analysis.findings}

    if doc.source_format == FORMAT_XML_TEMPLATE:
        return _generate_from_xml(doc, analysis, by_id)
    if doc.source_format == FORMAT_STUDIO_SPEC:
        return _generate_studio_spec(doc, analysis, by_id)
    return _generate_from_json(doc, analysis, by_id)


def _generate_from_json(
    doc: FlowDocument, analysis: MigrationAnalysis, by_id: dict[str, ComponentFinding]
) -> MigrationResult:
    migrated = copy.deepcopy(doc.original)
    result = MigrationResult(migrated=migrated, output_format=FORMAT_JSON_SNAPSHOT)
    target = get_version(analysis.target_version)

    def visit(node: dict[str, Any]) -> None:
        for proc in node.get("processors") or []:
            _apply_to_node(proc, by_id, result, target, kind="processor")
        for svc in node.get("controllerServices") or []:
            _apply_to_node(svc, by_id, result, target, kind="controllerService")
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

    _stamp_provenance(migrated, analysis)
    return result


def _apply_to_node(
    node: dict[str, Any],
    by_id: dict[str, ComponentFinding],
    result: MigrationResult,
    target: Any,
    kind: str,
) -> None:
    identifier = str(node.get("identifier") or node.get("id") or "")
    finding = by_id.get(identifier)
    if finding is None:
        return

    label = node.get("name") or finding.name

    # Never rewrite something that has no trustworthy mechanical fix. Mark it.
    if finding.block_auto_migration:
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
        comments = str(node.get("comments") or "")
        banner = (
            f"[MANUAL REVIEW REQUIRED — {finding.outcome}] {finding.explanation} "
            f"{finding.recommendation}".strip()
        )
        node["comments"] = f"{banner}\n\n{comments}".strip()
        result.skipped.append(
            {
                "component": label,
                "reason": "needs manual review; left unchanged deliberately",
                "outcome": finding.outcome,
            }
        )
        return

    # Unverified but not known-broken: apply the safe changes and leave a note
    # so whoever imports this flow knows what was and was not checked.
    if finding.outcome == UNKNOWN:
        comments = str(node.get("comments") or "")
        node["comments"] = (
            f"[NOT VERIFIED] {finding.explanation}\n\n{comments}".strip()
        )

    # Type rename/replacement that the rule base considers safe.
    if finding.target_type and finding.outcome in (RENAMED, REPLACED):
        old_type = node.get("type")
        node["type"] = finding.target_type
        result.applied_changes.append(
            {
                "component": label,
                "change": "type",
                "from": old_type,
                "to": finding.target_type,
                "reason": finding.explanation,
            }
        )

    # Property renames.
    props = node.get("properties")
    if isinstance(props, dict):
        for change in finding.property_changes:
            old = change.get("from")
            new = change.get("to")
            if change.get("property") == "schedulingStrategy":
                continue  # handled below; not a property-map entry
            if old in props and new and new != "(removed)":
                props[new] = props.pop(old)
                result.applied_changes.append(
                    {
                        "component": label,
                        "change": "property",
                        "from": old,
                        "to": new,
                        "reason": change.get("reason", ""),
                    }
                )
            elif old in props and new == "(removed)":
                props.pop(old)
                result.applied_changes.append(
                    {
                        "component": label,
                        "change": "property-removed",
                        "from": old,
                        "to": None,
                        "reason": change.get("reason", ""),
                    }
                )

    # Scheduling strategy.
    for change in finding.property_changes:
        if change.get("property") == "schedulingStrategy":
            node["schedulingStrategy"] = change.get("to")
            result.applied_changes.append(
                {
                    "component": label,
                    "change": "schedulingStrategy",
                    "from": change.get("from"),
                    "to": change.get("to"),
                    "reason": change.get("reason", ""),
                }
            )

    # Re-stamp the NAR bundle so the target loads the right version.
    bundle = node.get("bundle")
    if isinstance(bundle, dict) and target is not None and bundle.get("version"):
        old_version = bundle.get("version")
        if old_version != target.version:
            bundle["version"] = target.version
            result.applied_changes.append(
                {
                    "component": label,
                    "change": "bundleVersion",
                    "from": old_version,
                    "to": target.version,
                    "reason": "NAR bundle version follows the NiFi release",
                }
            )


def _generate_from_xml(
    doc: FlowDocument, analysis: MigrationAnalysis, by_id: dict[str, ComponentFinding]
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
        _apply_to_node(node, by_id, result, target, kind=kind)

    migrated = snapshot_from_xml_template(
        doc.xml_root,
        target.version if target else analysis.target_version,
        apply=apply,
        comments=(
            f"Migrated from a NiFi {analysis.source_version} XML template to a NiFi "
            f"{analysis.target_version} flow definition by Flow Studio "
            f"(source file: {analysis.filename}). NiFi 2.x cannot import XML templates, so "
            "the template's contents were converted to this flow definition. Components are "
            "imported stopped — review them before starting the flow."
        ),
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


def _apply_finding_to_plain(
    node: dict[str, Any],
    finding: ComponentFinding | None,
    result: MigrationResult,
    target: Any,
) -> None:
    if finding is None:
        return
    label = node.get("name") or finding.name
    if finding.block_auto_migration:
        result.manual_markers.append(
            {
                "component": label,
                "identifier": finding.identifier,
                "outcome": finding.outcome,
                "sourceType": finding.source_type,
                "suggestedType": finding.target_type,
                "reason": finding.explanation,
                "recommendation": finding.recommendation,
            }
        )
        node["comments"] = (
            f"[MANUAL REVIEW REQUIRED — {finding.outcome}] {finding.explanation} "
            f"{finding.recommendation}"
        ).strip()
        result.skipped.append(
            {
                "component": label,
                "reason": "needs manual review; left unchanged deliberately",
                "outcome": finding.outcome,
            }
        )
        return

    if finding.outcome == UNKNOWN:
        node["comments"] = f"[NOT VERIFIED] {finding.explanation}".strip()

    if finding.target_type and finding.outcome in (RENAMED, REPLACED):
        old = node.get("type")
        node["type"] = finding.target_type
        result.applied_changes.append(
            {"component": label, "change": "type", "from": old, "to": finding.target_type,
             "reason": finding.explanation}
        )

    props = node.get("properties") or {}
    for change in finding.property_changes:
        old, new = change.get("from"), change.get("to")
        if change.get("property") == "schedulingStrategy":
            node["schedulingStrategy"] = new
            result.applied_changes.append(
                {"component": label, "change": "schedulingStrategy", "from": old, "to": new,
                 "reason": change.get("reason", "")}
            )
            continue
        if old in props and new and new != "(removed)":
            props[new] = props.pop(old)
            result.applied_changes.append(
                {"component": label, "change": "property", "from": old, "to": new,
                 "reason": change.get("reason", "")}
            )


def _generate_studio_spec(
    doc: FlowDocument, analysis: MigrationAnalysis, by_id: dict[str, ComponentFinding]
) -> MigrationResult:
    """Migrate this application's own spec format, keeping it in that format."""
    migrated = copy.deepcopy(doc.original)
    result = MigrationResult(migrated=migrated, output_format=FORMAT_STUDIO_SPEC)
    target = get_version(analysis.target_version)

    def visit(group: dict[str, Any]) -> None:
        for proc in group.get("processors") or []:
            finding = by_id.get(str(proc.get("name") or ""))
            _apply_finding_to_plain(proc, finding, result, target)
        for svc in group.get("controllerServices") or []:
            finding = by_id.get(str(svc.get("name") or ""))
            _apply_finding_to_plain(svc, finding, result, target)
        for child in group.get("processGroups") or []:
            visit(child)

    visit(migrated)
    if target is not None:
        migrated["nifiVersion"] = target.version
    _stamp_provenance(migrated, analysis)
    return result


def _stamp_provenance(migrated: Any, analysis: MigrationAnalysis) -> None:
    """Record where the migrated artifact came from, inside the artifact itself.

    Written into the root group's `comments` rather than as a top-level key.
    NiFi deserializes a flow definition into its `VersionedFlowSnapshot` model
    and rejects the entire file when it meets a field it does not recognise, so
    an extra metadata key — however harmless it looks — makes the flow
    unimportable. The provenance also belongs where an operator will actually
    see it: on the group, in the NiFi UI.
    """
    if not isinstance(migrated, dict):
        return

    note = (
        f"[Flow Studio migration] NiFi {analysis.source_version} → {analysis.target_version}, "
        f"from {analysis.filename} ({analysis.source_format})."
    )
    root = None
    for key in ("flowContents", "rootGroup"):
        if isinstance(migrated.get(key), dict):
            root = migrated[key]
            break
    if root is None:
        # This application's own spec format: a metadata key is safe here,
        # because the spec is only ever read back by this codebase.
        migrated["flowStudioMigration"] = {
            "sourceVersion": analysis.source_version,
            "targetVersion": analysis.target_version,
            "sourceFile": analysis.filename,
            "sourceFormat": analysis.source_format,
            "generatedBy": "Flow Studio migration engine",
        }
        return

    existing = str(root.get("comments") or "")
    root["comments"] = f"{note}\n\n{existing}".strip()


def migrated_json_text(result: MigrationResult) -> str:
    return json.dumps(result.migrated, indent=2, ensure_ascii=False)
