"""Per-version JSON and XML dialects for NiFi flow files.

NiFi does not keep one flow-definition schema forever. JSON keys added after 2.6
are rejected (or ignored unpredictably) on 2.6, and older XML templates used
different attribute / collection tag names than later 1.x encodings.

This module is the allowlist:

  * parse accepts aliases so an older file still loads
  * generate emits only the target dialect, and fills required later fields
    with safe defaults when moving *forward*
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from nifi_versions import parse_version

# --- JSON flow definition -----------------------------------------------------

JSON_SNAPSHOT_BASE: frozenset[str] = frozenset(
    {
        "flowContents",
        "externalControllerServices",
        "parameterContexts",
        "flowEncodingVersion",
        "latest",
    }
)
JSON_SNAPSHOT_2X: frozenset[str] = JSON_SNAPSHOT_BASE | frozenset({"parameterProviders"})
# Keys that exist only on NiFi *after* 2.6. Targeting 2.6 strips them.
JSON_SNAPSHOT_AFTER_26: frozenset[str] = JSON_SNAPSHOT_2X | frozenset({"flowStatus"})

JSON_GROUP_BASE: frozenset[str] = frozenset(
    {
        "identifier",
        "instanceIdentifier",
        "name",
        "comments",
        "componentType",
        "groupIdentifier",
        "position",
        "processors",
        "controllerServices",
        "connections",
        "inputPorts",
        "outputPorts",
        "funnels",
        "labels",
        "processGroups",
        "remoteProcessGroups",
        "variables",
        "flowFileConcurrency",
        "flowFileOutboundPolicy",
        "defaultFlowFileExpiration",
        "defaultBackPressureObjectThreshold",
        "defaultBackPressureDataSizeThreshold",
        "parameterContextName",
    }
)
# 2.0 removed the Variable Registry, so `variables` is not part of a 2.x group.
JSON_GROUP_2X: frozenset[str] = (JSON_GROUP_BASE - frozenset({"variables"})) | frozenset(
    {"executionEngine", "statelessFlowTimeout"}
)
JSON_GROUP_AFTER_26: frozenset[str] = JSON_GROUP_2X | frozenset(
    {"defaultBackoffMechanism", "maxConcurrentTasks"}
)

# Defaults applied when targeting a release *after* 2.6 and the source omitted them.
JSON_GROUP_AFTER_26_DEFAULTS: dict[str, Any] = {
    "defaultBackoffMechanism": "PENALIZE_FLOWFILE",
    "maxConcurrentTasks": 1,
}

JSON_COLLECTION_KEYS: frozenset[str] = frozenset(
    {
        "processors",
        "controllerServices",
        "connections",
        "inputPorts",
        "outputPorts",
        "funnels",
        "labels",
        "processGroups",
        "remoteProcessGroups",
    }
)


def json_snapshot_keys(target_version: str) -> frozenset[str]:
    major, minor, _ = parse_version(target_version)
    if major < 2:
        return JSON_SNAPSHOT_BASE
    if (major, minor) <= (2, 6):
        return JSON_SNAPSHOT_2X
    return JSON_SNAPSHOT_AFTER_26


def json_group_keys(target_version: str) -> frozenset[str]:
    major, minor, _ = parse_version(target_version)
    if major < 2:
        return JSON_GROUP_BASE
    if (major, minor) <= (2, 6):
        return JSON_GROUP_2X
    return JSON_GROUP_AFTER_26


def apply_json_dialect(snapshot: dict[str, Any], target_version: str) -> list[str]:
    """Mutate `snapshot` so it matches the target JSON dialect.

    Returns human-readable notes about keys that were stripped or filled in.
    Unknown extra keys are dropped rather than copied through — NiFi rejects a
    flow definition that contains a field it does not recognise.
    """
    notes: list[str] = []
    allowed_top = json_snapshot_keys(target_version)
    extra_top = [key for key in list(snapshot) if key not in allowed_top]
    for key in extra_top:
        snapshot.pop(key, None)
        notes.append(
            f"Dropped JSON snapshot field {key!r} — not part of the NiFi {target_version} dialect."
        )

    root = None
    for key in ("flowContents", "rootGroup"):
        if isinstance(snapshot.get(key), dict):
            root = snapshot[key]
            break
    if isinstance(root, dict):
        _apply_group_dialect(root, target_version, notes)
    return notes


def _apply_group_dialect(group: dict[str, Any], target_version: str, notes: list[str]) -> None:
    allowed = json_group_keys(target_version)
    extra = [key for key in list(group) if key not in allowed]
    for key in extra:
        group.pop(key, None)
        name = group.get("name") or "process group"
        notes.append(
            f"Dropped {key!r} on group {name!r} — not part of the NiFi {target_version} dialect."
        )

    major, minor, _ = parse_version(target_version)
    if (major, minor) > (2, 6):
        for key, default in JSON_GROUP_AFTER_26_DEFAULTS.items():
            if key not in group:
                group[key] = default
                name = group.get("name") or "process group"
                notes.append(
                    f"Filled {key!r} on group {name!r} with default {default!r} "
                    f"required by NiFi {target_version}."
                )

    for child in group.get("processGroups") or []:
        if isinstance(child, dict):
            _apply_group_dialect(child, target_version, notes)


# --- XML templates ------------------------------------------------------------

# Canonical collection tag → aliases accepted on parse (older encodings used
# the singular form, and some exports camelCase the encoding attribute).
XML_COLLECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "processGroups": ("processGroups", "processGroup"),
    "processors": ("processors", "processor"),
    "controllerServices": ("controllerServices", "controllerService"),
    "connections": ("connections", "connection"),
    "inputPorts": ("inputPorts", "inputPort"),
    "outputPorts": ("outputPorts", "outputPort"),
    "funnels": ("funnels", "funnel"),
    "labels": ("labels", "label"),
    "variables": ("variables", "variable"),
}

XML_ENCODING_ATTR_ALIASES: tuple[str, ...] = ("encoding-version", "encodingVersion")


def xml_encoding_for_version(nifi_version: str) -> str:
    """Template encoding-version written for a 1.x target."""
    if parse_version(nifi_version) < (1, 21, 0):
        return "1.2"
    return "1.3"


def xml_encoding_attribute() -> str:
    """Canonical attribute name written on generate (never the camelCase alias)."""
    return "encoding-version"


def xml_children(node: ET.Element, canonical: str) -> list[ET.Element]:
    """Find children under the canonical tag or any known alias."""
    names = XML_COLLECTION_ALIASES.get(canonical, (canonical,))
    out: list[ET.Element] = []
    seen: set[int] = set()
    for name in names:
        for child in node.findall(name):
            ident = id(child)
            if ident not in seen:
                seen.add(ident)
                out.append(child)
    return out


def xml_child(node: ET.Element | None, *names: str) -> ET.Element | None:
    if node is None:
        return None
    for name in names:
        found = node.find(name)
        if found is not None:
            return found
    return None


def xml_encoding_of(root: ET.Element) -> str:
    for attr in XML_ENCODING_ATTR_ALIASES:
        value = (root.get(attr) or "").strip()
        if value:
            return value
    return ""
