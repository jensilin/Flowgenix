"""Deterministic NiFi migration rules.

This is the authoritative, hand-curated knowledge base the migration engine
consults. Compatibility is decided from these rules plus structural checks —
never from a live NiFi instance or a language model.

    1. These curated rules (known renames / removals / replacements / property
       changes, each with an explanation)
    2. Structural checks (EVENT_DRIVEN, Variable Registry, templates)

Adding coverage is meant to be mechanical: append a `ProcessorRule` or
`PropertyRule` and it takes effect immediately, including in the generated flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nifi_versions import parse_version

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

#: Property renames needed when moving a flow *down* across a boundary: the
#: source release uses the newer name and the older target only knows the old
#: one. NiFi migrates old names forward on import, but never backward.
DOWNGRADE_PROPERTY_RULES: tuple[PropertyRule, ...] = (
    PropertyRule(
        processor="JoltTransformJSON",
        old_name="Jolt Transform",
        new_name="Jolt Transformation DSL",
        applies_from=_TO_2X,
        explanation="NiFi 1.x knows this property by its pre-2.0 name.",
    ),
    PropertyRule(
        processor="JoltTransformRecord",
        old_name="Jolt Transform",
        new_name="Jolt Transformation DSL",
        applies_from=_TO_2X,
        explanation="NiFi 1.x knows this property by its pre-2.0 name.",
    ),
    *(
        PropertyRule(
            processor="InvokeHTTP",
            old_name=new,
            new_name=old,
            applies_from=_TO_2X,
            explanation="NiFi 2.0 renamed this InvokeHTTP property; 1.x only knows the old name.",
        )
        for new, old in (
            ("HTTP URL", "Remote URL"),
            ("Socket Connect Timeout", "Connection Timeout"),
            ("Socket Read Timeout", "Read Timeout"),
            ("Request Username", "Basic Authentication Username"),
            ("Request Password", "Basic Authentication Password"),
        )
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


# --- Component history --------------------------------------------------------
#
# When each component type arrived, was deprecated and was removed, and what to
# use instead. This is what makes suggestions direction-aware: the same table
# answers "the target removed this" on an upgrade and "the target predates this"
# on a downgrade. Versions come from the Apache NiFi wiki pages "Deprecated
# Components and Features" and "Migrating Deprecated Components and Features for
# 2.0.0".

_STD = "org.apache.nifi.processors.standard."


@dataclass(frozen=True)
class ComponentHistory:
    type_name: str  # fully-qualified class name
    kind: str = "processor"  # processor | controllerService
    introduced: str = "0.0.0"
    deprecated: str | None = None
    removed: str | None = None
    #: Fully-qualified replacement type, used when the target deprecated or removed this one.
    replacement: str | None = None
    #: Fully-qualified equivalent on releases that predate this type.
    downgrade_to: str | None = None
    #: How a swap carries configuration over, keyed by the type being swapped *to*.
    swaps: dict[str, "Swap"] = field(default_factory=dict)
    note: str = ""

    @property
    def short(self) -> str:
        return _short(self.type_name)

    def available_on(self, version: str) -> bool:
        v = parse_version(version)
        if v < parse_version(self.introduced):
            return False
        return not (self.removed and v >= parse_version(self.removed))


@dataclass(frozen=True)
class Swap:
    """How to carry a component's configuration over to a different type.

    Properties not named in `property_map` are dropped unless
    `carry_other_properties` is set: an unknown key on the new type becomes a
    dynamic property, which for InvokeHTTP means an HTTP header.
    """

    property_map: dict[str, str] = field(default_factory=dict)
    set_properties: dict[str, str] = field(default_factory=dict)
    value_map: dict[str, dict[str, str]] = field(default_factory=dict)
    relationship_map: dict[str, str] = field(default_factory=dict)
    carry_other_properties: bool = False
    bundle_artifact: str | None = None
    new_required_properties: tuple[str, ...] = ()
    risk: str = "review"  # safe | review
    note: str = ""


_HTTP_COMMON = {
    "URL": "Remote URL",
    "SSL Context Service": "SSL Context Service",
    "Connection Timeout": "Connection Timeout",
    "Data Timeout": "Read Timeout",
    "Username": "Basic Authentication Username",
    "Password": "Basic Authentication Password",
}

_JMS_NAR = "nifi-jms-processors-nar"

COMPONENT_HISTORY: tuple[ComponentHistory, ...] = (
    ComponentHistory(
        type_name=_STD + "GetHTTP",
        deprecated="1.9.0",
        removed=_TO_2X,
        replacement=_STD + "InvokeHTTP",
        swaps={
            _STD + "InvokeHTTP": Swap(
                property_map=_HTTP_COMMON,
                set_properties={"HTTP Method": "GET"},
                relationship_map={"success": "Response"},
                note=(
                    "InvokeHTTP also emits Original, Retry, No Retry and Failure; connect or "
                    "auto-terminate them. The FlowFile filename is no longer taken from the "
                    "Filename property."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "PostHTTP",
        deprecated="1.9.0",
        removed=_TO_2X,
        replacement=_STD + "InvokeHTTP",
        swaps={
            _STD + "InvokeHTTP": Swap(
                property_map=_HTTP_COMMON,
                set_properties={"HTTP Method": "POST"},
                relationship_map={"success": "Original", "failure": "Failure"},
                note=(
                    "InvokeHTTP also emits Response, Retry and No Retry; connect or "
                    "auto-terminate them. 'Send as FlowFile' packaging has no InvokeHTTP "
                    "equivalent — use MergeContent (FlowFile v3) before it if you relied on it."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "Base64EncodeContent",
        deprecated="1.20.0",
        removed=_TO_2X,
        replacement=_STD + "EncodeContent",
        swaps={
            _STD + "EncodeContent": Swap(
                property_map={"Mode": "Mode"},
                set_properties={"Encoding": "base64"},
                risk="safe",
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "HashContent",
        deprecated="1.8.0",
        removed=_TO_2X,
        replacement=_STD + "CryptographicHashContent",
        swaps={
            _STD + "CryptographicHashContent": Swap(
                property_map={"Hash Algorithm": "Hash Algorithm"},
                note=(
                    "CryptographicHashContent writes the digest to content_<algorithm> "
                    "instead of the configured Hash Attribute Name; add an UpdateAttribute "
                    "if downstream components read the old attribute."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "CryptographicHashContent",
        introduced="1.8.0",
        downgrade_to=_STD + "HashContent",
        swaps={
            _STD + "HashContent": Swap(
                property_map={"Hash Algorithm": "Hash Algorithm"},
                note="HashContent writes the digest to hash.value unless Hash Attribute Name is set.",
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "HashAttribute",
        deprecated="1.21.0",
        removed=_TO_2X,
        replacement=_STD + "UpdateAttribute",
        swaps={
            _STD + "UpdateAttribute": Swap(
                note=(
                    "HashAttribute's settings do not translate. Add a dynamic property on "
                    "UpdateAttribute that uses the hash() expression function, for example "
                    "label-hash = ${label:hash('SHA-256')}."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "CryptographicHashAttribute",
        introduced="1.8.0",
        deprecated="1.21.0",
        removed=_TO_2X,
        replacement=_STD + "UpdateAttribute",
        swaps={
            _STD + "UpdateAttribute": Swap(
                note="Use the hash() expression function in an UpdateAttribute dynamic property.",
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "GetJMSQueue",
        deprecated="1.3.0",
        removed=_TO_2X,
        replacement="org.apache.nifi.jms.processors.ConsumeJMS",
        swaps={
            "org.apache.nifi.jms.processors.ConsumeJMS": Swap(
                property_map={
                    "Destination Name": "Destination Name",
                    "Username": "User Name",
                    "Password": "Password",
                },
                set_properties={"Destination Type": "QUEUE"},
                bundle_artifact=_JMS_NAR,
                new_required_properties=("Connection Factory Service",),
                note=(
                    "ConsumeJMS needs a JMSConnectionFactoryProvider controller service and a "
                    "directory of JMS client libraries; the broker URL moves to that service."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "GetJMSTopic",
        deprecated="1.3.0",
        removed=_TO_2X,
        replacement="org.apache.nifi.jms.processors.ConsumeJMS",
        swaps={
            "org.apache.nifi.jms.processors.ConsumeJMS": Swap(
                property_map={
                    "Destination Name": "Destination Name",
                    "Username": "User Name",
                    "Password": "Password",
                },
                set_properties={"Destination Type": "TOPIC"},
                bundle_artifact=_JMS_NAR,
                new_required_properties=("Connection Factory Service",),
                note=(
                    "ConsumeJMS needs a JMSConnectionFactoryProvider controller service and a "
                    "directory of JMS client libraries; the broker URL moves to that service."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name=_STD + "PutJMS",
        deprecated="1.3.0",
        removed=_TO_2X,
        replacement="org.apache.nifi.jms.processors.PublishJMS",
        swaps={
            "org.apache.nifi.jms.processors.PublishJMS": Swap(
                property_map={
                    "Destination Name": "Destination Name",
                    "Destination Type": "Destination Type",
                    "Username": "User Name",
                    "Password": "Password",
                },
                value_map={"Destination Type": {"Queue": "QUEUE", "Topic": "TOPIC"}},
                bundle_artifact=_JMS_NAR,
                new_required_properties=("Connection Factory Service",),
                note=(
                    "PublishJMS needs a JMSConnectionFactoryProvider controller service and a "
                    "directory of JMS client libraries; the broker URL moves to that service."
                ),
            )
        },
    ),
    ComponentHistory(
        type_name="org.apache.nifi.kafka.processors.ConsumeKafka",
        introduced=_TO_2X,
        note=(
            "The 2.x Kafka processors delegate the connection to a Kafka3ConnectionService. "
            "The 1.x ConsumeKafka_* processors are configured differently, so rebuild this "
            "component by hand on the older release."
        ),
    ),
    ComponentHistory(
        type_name="org.apache.nifi.kafka.processors.PublishKafka",
        introduced=_TO_2X,
        note=(
            "The 2.x Kafka processors delegate the connection to a Kafka3ConnectionService. "
            "The 1.x PublishKafka_* processors are configured differently, so rebuild this "
            "component by hand on the older release."
        ),
    ),
    ComponentHistory(
        type_name="org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService",
        kind="controllerService",
        deprecated=_TO_2X,
        replacement="org.apache.nifi.distributed.cache.client.MapCacheClientService",
        swaps={
            "org.apache.nifi.distributed.cache.client.MapCacheClientService": Swap(
                carry_other_properties=True,
                risk="safe",
            )
        },
    ),
    ComponentHistory(
        type_name="org.apache.nifi.distributed.cache.client.MapCacheClientService",
        kind="controllerService",
        introduced=_TO_2X,
        downgrade_to="org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService",
        swaps={
            "org.apache.nifi.distributed.cache.client.DistributedMapCacheClientService": Swap(
                carry_other_properties=True,
                risk="safe",
            )
        },
    ),
)


@dataclass(frozen=True)
class LifecycleVerdict:
    """What the component history says about one type on one migration path."""

    status: str  # removed | deprecated | not_available | available
    direction: str  # upgrade | downgrade
    history: ComponentHistory
    swap_to: str | None = None
    swap: Swap | None = None
    explanation: str = ""

    @property
    def required(self) -> bool:
        return self.status in ("removed", "not_available")


def component_history_for(type_name: str) -> ComponentHistory | None:
    """Match on the full class name when one is given, otherwise on the short name.

    The full name matters: 1.x shipped a Kafka 0.9 `ConsumeKafka` in another
    package, which is not the 2.x processor of the same short name.
    """
    name = (type_name or "").strip()
    if "." in name:
        for entry in COMPONENT_HISTORY:
            if entry.type_name == name:
                return entry
        return None
    for entry in COMPONENT_HISTORY:
        if entry.short.lower() == name.lower():
            return entry
    return None


def _swap_available(type_name: str | None, target_version: str) -> bool:
    if not type_name:
        return False
    entry = component_history_for(type_name)
    return entry is None or entry.available_on(target_version)


def lifecycle_verdict(
    type_name: str, source_version: str, target_version: str
) -> LifecycleVerdict | None:
    entry = component_history_for(type_name)
    if entry is None:
        return None
    t = parse_version(target_version)
    direction = "downgrade" if t < parse_version(source_version) else "upgrade"

    if t < parse_version(entry.introduced):
        alt = entry.downgrade_to if _swap_available(entry.downgrade_to, target_version) else None
        text = f"{entry.short} was introduced in NiFi {entry.introduced}; NiFi {target_version} does not have it."
        if alt:
            text += f" {_short(alt)} is the closest equivalent on that release."
        else:
            text += " There is no equivalent on that release."
        if entry.note:
            text += f" {entry.note}"
        return LifecycleVerdict(
            "not_available", direction, entry, alt, entry.swaps.get(alt) if alt else None, text
        )

    alt = entry.replacement if _swap_available(entry.replacement, target_version) else None
    swap = entry.swaps.get(alt) if alt else None
    if entry.removed and t >= parse_version(entry.removed):
        text = (
            f"{entry.short} was deprecated in NiFi {entry.deprecated} and removed in NiFi "
            f"{entry.removed.split('.')[0]}.0."
            if entry.deprecated
            else f"{entry.short} was removed in NiFi {entry.removed}."
        )
        if alt:
            text += f" {_short(alt)} is the supported replacement."
        return LifecycleVerdict("removed", direction, entry, alt, swap, text)

    if entry.deprecated and t >= parse_version(entry.deprecated):
        text = (
            f"{entry.short} is deprecated since NiFi {entry.deprecated} but still available on "
            f"NiFi {target_version}."
        )
        if alt:
            text += f" {_short(alt)} is the recommended replacement; switching is optional."
        return LifecycleVerdict("deprecated", direction, entry, alt, swap, text)

    return LifecycleVerdict(
        "available",
        direction,
        entry,
        explanation=f"{entry.short} is available on NiFi {target_version}.",
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


def downgrade_property_rules_for(
    type_name: str, source_version: str, target_version: str
) -> list[PropertyRule]:
    """Renames to undo when the source is at or past a boundary the target is below."""
    short = _short(type_name).lower()
    source, target = parse_version(source_version), parse_version(target_version)
    return [
        rule
        for rule in DOWNGRADE_PROPERTY_RULES
        if source >= parse_version(rule.applies_from) > target
        and (rule.processor == "*" or _short(rule.processor).lower() == short)
    ]


def property_name_on(type_name: str, name: str, target_version: str) -> str:
    """The name `type_name` uses for a property on `target_version`.

    Swaps are written with pre-2.0 names; a 2.x target spells some of them differently.
    """
    short = _short(type_name).lower()
    target = parse_version(target_version)
    for rule in DOWNGRADE_PROPERTY_RULES:
        if (
            rule.new_name == name
            and target >= parse_version(rule.applies_from)
            and (rule.processor == "*" or _short(rule.processor).lower() == short)
        ):
            return rule.old_name
    return name


def rule_summary() -> dict[str, Any]:
    """Introspection for tests and the UI's 'what does this tool know?' hint."""
    history = [h for h in COMPONENT_HISTORY if h.kind == "processor"]
    return {
        "processorRules": len(PROCESSOR_RULES),
        "propertyRules": len(PROPERTY_RULES) + len(DOWNGRADE_PROPERTY_RULES),
        "serviceRules": len(SERVICE_RULES),
        "historyEntries": len(COMPONENT_HISTORY),
        "coveredProcessors": sorted(
            {_short(r.source_name) for r in PROCESSOR_RULES} | {h.short for h in history}
        ),
        "coveredServices": sorted(
            {_short(r.source_name) for r in SERVICE_RULES}
            | {h.short for h in COMPONENT_HISTORY if h.kind == "controllerService"}
        ),
    }
