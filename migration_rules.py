"""Deterministic NiFi migration rules.

This is the authoritative, hand-curated knowledge base the migration engine
consults *before* it asks an AI model anything. The user's requirement is
explicit: AI assists, but it is never the sole source of truth for NiFi
compatibility. So the precedence the engine applies is:

    1. A live NiFi catalog at the target version (absolute ground truth: either
       the instance has the processor type installed or it does not)
    2. These curated rules (known renames / removals / replacements / property
       changes, each with a citation-worthy explanation)
    3. The AI model (explanation and suggestions for what neither 1 nor 2 knows,
       always surfaced to the user as "AI suggestion, needs review")

Adding coverage is meant to be mechanical: append a `ProcessorRule` or
`PropertyRule` and it takes effect immediately, including in the generated flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nifi_catalog import parse_version

# --- Outcome vocabulary -------------------------------------------------------
#
# Every analysed component lands in exactly one of these buckets. The UI summary
# counts them and the detail table explains them, so keep the set small.
COMPATIBLE = "compatible"
CONFIG_CHANGE = "config_change"
REPLACED = "replaced"
RENAMED = "renamed"
DEPRECATED = "deprecated"
REMOVED = "removed"
MANUAL = "manual_review"
UNKNOWN = "unknown"

OUTCOME_ORDER = (
    REMOVED,
    MANUAL,
    REPLACED,
    RENAMED,
    CONFIG_CHANGE,
    DEPRECATED,
    UNKNOWN,
    COMPATIBLE,
)

#: Outcomes that mean "there is a known problem a human must resolve".
#:
#: `UNKNOWN` is deliberately excluded. It means "we could not verify this type",
#: which is a gap in *our* evidence rather than a defect in the flow, and it gets
#: its own count and report section. Lumping it in here would mark every
#: component of every offline analysis as broken.
NEEDS_REVIEW = frozenset({REMOVED, MANUAL})

#: Outcomes worth asking an AI model about: a known problem, or an unverified type.
NEEDS_INVESTIGATION = frozenset({REMOVED, MANUAL, UNKNOWN})


@dataclass(frozen=True)
class ProcessorRule:
    """How one processor type changes between two NiFi version ranges.

    `applies_from` / `applies_to` bound the *target* version this rule speaks
    about, expressed as inclusive lower / exclusive upper bounds. A rule with
    `applies_from="2.0.0"` describes what happens when migrating **to** 2.x.
    """

    source_name: str
    outcome: str
    applies_from: str = "0.0.0"
    applies_to: str | None = None
    replacement: str | None = None
    explanation: str = ""
    #: Property renames to apply when this rule fires: {old: new}.
    property_renames: dict[str, str] = field(default_factory=dict)
    #: Properties the target requires that the source flow will not have.
    new_required_properties: tuple[str, ...] = ()
    #: Properties the target no longer understands; dropped from the migrated flow.
    removed_properties: tuple[str, ...] = ()
    #: True when a mechanical rewrite is unsafe even though a replacement exists.
    manual_review: bool = False

    def targets(self, target_version: str) -> bool:
        tv = parse_version(target_version)
        if tv < parse_version(self.applies_from):
            return False
        if self.applies_to and tv >= parse_version(self.applies_to):
            return False
        return True


@dataclass(frozen=True)
class PropertyRule:
    """A property rename/removal that applies to a processor type regardless of
    whether the processor itself changed.

    NiFi renames display names between releases far more often than it renames
    processors; `Jolt Transformation DSL` → `Jolt Transform` in 2.x is the
    canonical example, and a silently-dropped rename leaves the component
    invalid on the target.
    """

    processor: str  # short name, or "*" for any processor
    old_name: str
    new_name: str | None  # None means the property was removed
    applies_from: str = "0.0.0"
    applies_to: str | None = None
    explanation: str = ""

    def targets(self, target_version: str) -> bool:
        tv = parse_version(target_version)
        if tv < parse_version(self.applies_from):
            return False
        if self.applies_to and tv >= parse_version(self.applies_to):
            return False
        return True


# --- Processor rules ----------------------------------------------------------
#
# Scope note: these cover changes introduced by the NiFi 2.0 major release,
# which is where the breaking removals live. Same-line migrations (1.19 → 1.25)
# are overwhelmingly compatible and are handled by the "no rule + catalog says
# it exists" path rather than by enumerating every release here.

_TO_2X = "2.0.0"

PROCESSOR_RULES: tuple[ProcessorRule, ...] = (
    # --- Removed in 2.0: the deprecated HTTP pair -----------------------------
    ProcessorRule(
        source_name="GetHTTP",
        outcome=REMOVED,
        applies_from=_TO_2X,
        replacement="InvokeHTTP",
        explanation=(
            "GetHTTP was deprecated during the 1.x line and removed in NiFi 2.0. "
            "InvokeHTTP with HTTP Method=GET is the supported replacement, but it "
            "is a different processor with a different property set and emits "
            "Response/Retry/No Retry/Failure relationships instead of success."
        ),
        manual_review=True,
    ),
    ProcessorRule(
        source_name="PostHTTP",
        outcome=REMOVED,
        applies_from=_TO_2X,
        replacement="InvokeHTTP",
        explanation=(
            "PostHTTP was deprecated during the 1.x line and removed in NiFi 2.0. "
            "Use InvokeHTTP with HTTP Method=POST. Relationship names and the "
            "content-handling model differ, so the connections need review."
        ),
        manual_review=True,
    ),
    # --- Removed in 2.0: template-era and legacy components -------------------
    ProcessorRule(
        source_name="ListenEmptyFlowFile",
        outcome=REMOVED,
        applies_from=_TO_2X,
        explanation="Removed in NiFi 2.0 with no direct replacement.",
        manual_review=True,
    ),
    # --- Scripting: engines dropped in 2.0 ------------------------------------
    ProcessorRule(
        source_name="ExecuteScript",
        outcome=CONFIG_CHANGE,
        applies_from=_TO_2X,
        explanation=(
            "ExecuteScript still exists in 2.x, but the bundled script engines "
            "changed: Jython and JRuby were removed. A Groovy script migrates "
            "unchanged; a Python or Ruby script must be rewritten (2.x offers a "
            "separate native Python extension mechanism instead)."
        ),
        manual_review=True,
    ),
    ProcessorRule(
        source_name="InvokeScriptedProcessor",
        outcome=CONFIG_CHANGE,
        applies_from=_TO_2X,
        explanation=(
            "Bundled script engines changed in 2.0 (Jython and JRuby removed). "
            "Groovy scripts carry over; other languages need rewriting."
        ),
        manual_review=True,
    ),
)

# --- Property rules -----------------------------------------------------------

PROPERTY_RULES: tuple[PropertyRule, ...] = (
    PropertyRule(
        processor="JoltTransformJSON",
        old_name="Jolt Transformation DSL",
        new_name="Jolt Transform",
        applies_from=_TO_2X,
        explanation="NiFi 2.x renamed this property's display name.",
    ),
    PropertyRule(
        processor="JoltTransformRecord",
        old_name="Jolt Transformation DSL",
        new_name="Jolt Transform",
        applies_from=_TO_2X,
        explanation="NiFi 2.x renamed this property's display name.",
    ),
)

# --- Controller service rules -------------------------------------------------

SERVICE_RULES: tuple[ProcessorRule, ...] = (
    ProcessorRule(
        source_name="DistributedMapCacheClientService",
        outcome=DEPRECATED,
        applies_from=_TO_2X,
        explanation=(
            "Still present in 2.x, but the distributed cache components are "
            "deprecated in favour of external caches. Verify it is installed on "
            "the target instance before relying on it."
        ),
    ),
)


# --- Flow-level (non-processor) concerns --------------------------------------


@dataclass(frozen=True)
class FlowConcern:
    """A migration issue that is about the flow as a whole, not one component."""

    key: str
    title: str
    outcome: str
    explanation: str
    recommendation: str


def flow_concerns(source_version: str, target_version: str, source_format: str) -> list[FlowConcern]:
    """Whole-flow issues raised by crossing from `source_version` to `target_version`.

    These are the ones that bite hardest in a 1.x → 2.x move and that no
    per-processor rule would catch.
    """
    from nifi_versions import get_version

    concerns: list[FlowConcern] = []
    target = get_version(target_version)
    if target is None:
        return concerns

    crossing_to_2x = parse_version(target_version)[0] >= 2 and parse_version(source_version)[0] < 2

    if crossing_to_2x and source_format == "xml_template":
        concerns.append(
            FlowConcern(
                key="templates_removed",
                title="XML templates are not supported on NiFi 2.x",
                outcome=MANUAL,
                explanation=(
                    "NiFi 2.0 removed the template feature entirely — there is no "
                    "'Upload Template' action and the /templates REST endpoints are gone. "
                    "An XML template cannot be imported into a 2.x canvas as-is."
                ),
                recommendation=(
                    "This tool converts the template's contents into a 2.x flow definition "
                    "(JSON) so the components can be recreated. Import the generated JSON "
                    "via NiFi Registry or the 'Import from JSON' option on the 2.x canvas."
                ),
            )
        )

    if crossing_to_2x:
        concerns.append(
            FlowConcern(
                key="variable_registry_removed",
                title="Variable Registry was removed in NiFi 2.0",
                outcome=MANUAL,
                explanation=(
                    "Process-group Variables no longer exist in 2.x. Any property "
                    "referencing a variable as ${varName} will resolve as a FlowFile "
                    "attribute lookup instead, which usually yields an empty value."
                ),
                recommendation=(
                    "Move each variable into a Parameter Context and change the reference "
                    "from ${varName} to #{paramName}. This tool reports the affected "
                    "properties but will not rewrite them automatically, because ${...} is "
                    "also valid attribute-expression syntax and rewriting blindly would "
                    "corrupt genuine attribute lookups."
                ),
            )
        )
        concerns.append(
            FlowConcern(
                key="java_21",
                title="NiFi 2.x requires Java 21",
                outcome=MANUAL,
                explanation="The 2.x line dropped support for Java 8/11.",
                recommendation="Confirm the target host runs Java 21 before deploying the migrated flow.",
            )
        )

    return concerns


# --- Lookup helpers -----------------------------------------------------------


def _short(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1]


def processor_rule_for(type_name: str, target_version: str) -> ProcessorRule | None:
    short = _short(type_name)
    for rule in PROCESSOR_RULES:
        if _short(rule.source_name).lower() == short.lower() and rule.targets(target_version):
            return rule
    return None


def service_rule_for(type_name: str, target_version: str) -> ProcessorRule | None:
    short = _short(type_name)
    for rule in SERVICE_RULES:
        if _short(rule.source_name).lower() == short.lower() and rule.targets(target_version):
            return rule
    return None


def property_rules_for(type_name: str, target_version: str) -> list[PropertyRule]:
    short = _short(type_name).lower()
    return [
        rule
        for rule in PROPERTY_RULES
        if rule.targets(target_version)
        and (rule.processor == "*" or _short(rule.processor).lower() == short)
    ]


def rule_summary() -> dict[str, Any]:
    """Introspection for tests and the UI's 'what does this tool know?' hint."""
    return {
        "processorRules": len(PROCESSOR_RULES),
        "propertyRules": len(PROPERTY_RULES),
        "serviceRules": len(SERVICE_RULES),
        "coveredProcessors": sorted({_short(r.source_name) for r in PROCESSOR_RULES}),
        "coveredServices": sorted({_short(r.source_name) for r in SERVICE_RULES}),
    }
