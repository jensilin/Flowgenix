"""Parse and serialize the flow files NiFi exports.

Three formats matter for migration, and the format alone does *not* tell you the
NiFi version — which is the trap this module exists to avoid:

  * ``xml_template``   — NiFi 1.x "Download template" (.xml). 1.x only; NiFi 2.0
                         removed templates entirely.
  * ``json_snapshot``  — "Download flow definition" (.json). Produced by **both**
                         1.x (1.16+) and 2.x, so a .json upload must never be
                         assumed to be 2.x.
  * ``studio_spec``    — this application's own flow spec (see build_flow_from_spec).

Everything is normalized into `FlowDocument`, a version-agnostic tree of
`Component` records, so the migration engine works on one shape regardless of
what was uploaded. Serialization goes back out as a 2.x-compatible JSON flow
definition (or the original shape when migrating within a line).

Version detection is evidence-based and reports its confidence, because guessing
silently is how you corrupt someone's production flow.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from nifi_catalog import parse_version

FORMAT_XML_TEMPLATE = "xml_template"
FORMAT_JSON_SNAPSHOT = "json_snapshot"
FORMAT_STUDIO_SPEC = "studio_spec"

#: Confidence levels for a detected source version.
CONFIDENCE_CERTAIN = "certain"
CONFIDENCE_LIKELY = "likely"
CONFIDENCE_UNKNOWN = "unknown"


class FlowParseError(ValueError):
    """Raised when an upload cannot be understood as any supported flow format."""


@dataclass
class Component:
    """One processor, controller service, port, funnel, or connection.

    Deliberately flat and format-agnostic: `raw` keeps a reference to the
    original node so serialization can round-trip fields we do not model.
    """

    kind: str  # processor | controllerService | port | funnel | connection | label | rpg
    identifier: str
    name: str
    type_name: str = ""
    bundle: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    scheduling_strategy: str = ""
    scheduling_period: str = ""
    auto_terminated: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    group_path: str = "/"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def short_type(self) -> str:
        return (self.type_name or "").rsplit(".", 1)[-1]

    @property
    def bundle_version(self) -> str:
        return str((self.bundle or {}).get("version") or "")


@dataclass
class FlowDocument:
    """A parsed NiFi flow, normalized across formats."""

    source_format: str
    filename: str
    root_name: str = ""
    components: list[Component] = field(default_factory=list)
    parameter_contexts: list[dict[str, Any]] = field(default_factory=list)
    variables: list[dict[str, Any]] = field(default_factory=list)
    detected_version: str | None = None
    detection_confidence: str = CONFIDENCE_UNKNOWN
    detection_evidence: list[str] = field(default_factory=list)
    #: The untouched parsed payload. Never mutated — migration works on a copy.
    original: Any = None
    raw_text: str = ""
    #: Parsed XML tree for template uploads. Generation walks this rather than
    #: the flattened `components` list, because converting a template into a NiFi
    #: 2.x flow definition has to reproduce the process-group hierarchy and the
    #: per-component fields (position, style, relationships) that the flat model
    #: intentionally drops.
    xml_root: ET.Element | None = None

    def of_kind(self, kind: str) -> list[Component]:
        return [c for c in self.components if c.kind == kind]

    @property
    def processors(self) -> list[Component]:
        return self.of_kind("processor")

    @property
    def controller_services(self) -> list[Component]:
        return self.of_kind("controllerService")

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for c in self.components:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return {
            "filename": self.filename,
            "sourceFormat": self.source_format,
            "rootName": self.root_name,
            "componentCounts": counts,
            "totalComponents": len(self.components),
            "parameterContexts": len(self.parameter_contexts),
            "variables": len(self.variables),
            "detectedVersion": self.detected_version,
            "detectionConfidence": self.detection_confidence,
            "detectionEvidence": self.detection_evidence,
        }


# --- Entry point --------------------------------------------------------------


def parse_flow_file(text: str, filename: str = "flow.xml") -> FlowDocument:
    """Parse an uploaded NiFi export into a `FlowDocument`.

    Dispatches on *content*, not on the file extension, because users rename
    downloads and because a .json file may be either 1.x or 2.x.
    """
    cleaned = (text or "").lstrip("\ufeff").strip()
    if not cleaned:
        raise FlowParseError("Uploaded file is empty.")

    if cleaned.startswith("<"):
        return _parse_xml(cleaned, filename)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise FlowParseError(
            "File is neither XML nor valid JSON. Upload a NiFi template (.xml) "
            f"or a NiFi flow definition (.json). Parser said: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise FlowParseError(
            "JSON flow definitions are objects; this file's top level is a "
            f"{type(data).__name__}."
        )
    return _parse_json(data, cleaned, filename)


# --- XML templates (NiFi 1.x) -------------------------------------------------


def _parse_xml(text: str, filename: str) -> FlowDocument:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FlowParseError(f"Malformed XML: {exc}") from exc

    tag = _strip_ns(root.tag)
    if tag not in ("template", "flowController", "snippet"):
        raise FlowParseError(
            f"Unrecognised XML root <{tag}>. Expected a NiFi template "
            "(<template>) or flow (<flowController>)."
        )

    doc = FlowDocument(
        source_format=FORMAT_XML_TEMPLATE,
        filename=filename,
        original=text,
        raw_text=text,
        xml_root=root,
    )
    doc.root_name = _xml_text(root, "name") or _xml_text(root, "groupId") or "template"

    # A template wraps its content in <snippet>; a flow.xml has <rootGroup>.
    # `find` returns an Element whose truthiness reflects its child count, so
    # these must be compared against None explicitly.
    content = root.find("snippet")
    if content is None:
        content = root.find("rootGroup")
    if content is None:
        content = root
    _walk_xml_group(content, doc, group_path="/")

    _detect_version_xml(root, doc)
    return doc


def _walk_xml_group(node: ET.Element, doc: FlowDocument, group_path: str) -> None:
    for proc in node.findall("processors"):
        doc.components.append(_xml_processor(proc, group_path))
    for svc in node.findall("controllerServices"):
        doc.components.append(_xml_service(svc, group_path))
    for conn in node.findall("connections"):
        doc.components.append(_xml_connection(conn, group_path))
    for port_tag, kind in (("inputPorts", "port"), ("outputPorts", "port")):
        for port in node.findall(port_tag):
            doc.components.append(
                Component(
                    kind=kind,
                    identifier=_xml_text(port, "id"),
                    name=_xml_text(port, "name"),
                    type_name=port_tag.rstrip("s"),
                    group_path=group_path,
                    raw={"xmlTag": port_tag},
                )
            )
    for funnel in node.findall("funnels"):
        doc.components.append(
            Component(
                kind="funnel",
                identifier=_xml_text(funnel, "id"),
                name="funnel",
                group_path=group_path,
            )
        )
    for label in node.findall("labels"):
        doc.components.append(
            Component(
                kind="label",
                identifier=_xml_text(label, "id"),
                name=_xml_text(label, "label") or "label",
                group_path=group_path,
            )
        )

    for group in node.findall("processGroups"):
        name = _xml_text(group, "name") or "group"
        child_path = f"{group_path}{name}/"
        # Variables live on the group in 1.x and vanish in 2.x, so capture them.
        for var in group.findall("variables"):
            doc.variables.append(
                {
                    "name": _xml_text(var, "name"),
                    "value": _xml_text(var, "value"),
                    "groupPath": child_path,
                }
            )
        contents = group.find("contents")
        if contents is not None:
            _walk_xml_group(contents, doc, child_path)


def _xml_processor(node: ET.Element, group_path: str) -> Component:
    config = node.find("config")
    props: dict[str, Any] = {}
    auto_term: list[str] = []
    scheduling_strategy = ""
    scheduling_period = ""
    if config is not None:
        for entry in config.findall("properties/entry"):
            key = _xml_text(entry, "key")
            if key:
                props[key] = _xml_text(entry, "value")
        scheduling_strategy = _xml_text(config, "schedulingStrategy")
        scheduling_period = _xml_text(config, "schedulingPeriod")
        for rel in config.findall("autoTerminatedRelationships"):
            if rel.text:
                auto_term.append(rel.text)

    bundle_node = node.find("bundle")
    bundle: dict[str, Any] = {}
    if bundle_node is not None:
        bundle = {
            "group": _xml_text(bundle_node, "group"),
            "artifact": _xml_text(bundle_node, "artifact"),
            "version": _xml_text(bundle_node, "version"),
        }

    return Component(
        kind="processor",
        identifier=_xml_text(node, "id"),
        name=_xml_text(node, "name"),
        type_name=_xml_text(node, "type"),
        bundle=bundle,
        properties=props,
        scheduling_strategy=scheduling_strategy,
        scheduling_period=scheduling_period,
        auto_terminated=auto_term,
        group_path=group_path,
    )


def _xml_service(node: ET.Element, group_path: str) -> Component:
    props: dict[str, Any] = {}
    for entry in node.findall("properties/entry"):
        key = _xml_text(entry, "key")
        if key:
            props[key] = _xml_text(entry, "value")
    bundle_node = node.find("bundle")
    bundle: dict[str, Any] = {}
    if bundle_node is not None:
        bundle = {
            "group": _xml_text(bundle_node, "group"),
            "artifact": _xml_text(bundle_node, "artifact"),
            "version": _xml_text(bundle_node, "version"),
        }
    return Component(
        kind="controllerService",
        identifier=_xml_text(node, "id"),
        name=_xml_text(node, "name"),
        type_name=_xml_text(node, "type"),
        bundle=bundle,
        properties=props,
        group_path=group_path,
    )


def _xml_connection(node: ET.Element, group_path: str) -> Component:
    """Read a template connection.

    Endpoints are nested elements (`<source><id/><groupId/><type/></source>`),
    not the flat `<sourceId>` fields an earlier version of this parser looked
    for. Getting this wrong silently produced connections with empty endpoints,
    which is invisible in a component count but destroys the flow topology.
    """
    rels = [r.text for r in node.findall("selectedRelationships") if r.text]
    return Component(
        kind="connection",
        identifier=_xml_text(node, "id"),
        name=_xml_text(node, "name"),
        relationships=rels,
        group_path=group_path,
        raw={
            "source": _xml_connectable(node.find("source")),
            "destination": _xml_connectable(node.find("destination")),
        },
    )


def _xml_connectable(node: ET.Element | None) -> dict[str, str]:
    if node is None:
        return {}
    return {
        "id": _xml_text(node, "id"),
        "groupId": _xml_text(node, "groupId"),
        "type": _xml_text(node, "type"),
        "name": _xml_text(node, "name"),
    }


def _xml_text(node: ET.Element | None, child: str) -> str:
    if node is None:
        return ""
    found = node.find(child)
    return (found.text or "").strip() if found is not None and found.text else ""


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# --- JSON flow definitions (NiFi 1.x AND 2.x) ---------------------------------


def _parse_json(data: dict[str, Any], text: str, filename: str) -> FlowDocument:
    # This application's own spec shape — detect before the NiFi shapes so a
    # Flow Studio export round-trips instead of being misread as a NiFi export.
    if "processGroupName" in data and "processors" in data:
        return _parse_studio_spec(data, text, filename)

    root = _json_root_group(data)
    if root is None:
        raise FlowParseError(
            "JSON does not look like a NiFi flow definition. Expected a "
            "'flowContents' or 'rootGroup' object (NiFi 'Download flow definition')."
        )

    doc = FlowDocument(
        source_format=FORMAT_JSON_SNAPSHOT,
        filename=filename,
        original=data,
        raw_text=text,
    )
    doc.root_name = root.get("name") or "flow"
    _walk_json_group(root, doc, group_path="/")

    for ctx in _json_parameter_contexts(data, root):
        doc.parameter_contexts.append(ctx)

    _detect_version_json(data, doc)
    return doc


def _json_root_group(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("flowContents", "rootGroup"):
        node = data.get(key)
        if isinstance(node, dict):
            return node
    # A bare versioned process group (no wrapper) still has these markers.
    if data.get("componentType") == "PROCESS_GROUP" or "processors" in data:
        return data
    return None


def _walk_json_group(node: dict[str, Any], doc: FlowDocument, group_path: str) -> None:
    for proc in node.get("processors") or []:
        doc.components.append(_json_processor(proc, group_path))
    for svc in node.get("controllerServices") or []:
        doc.components.append(_json_service(svc, group_path))
    for conn in node.get("connections") or []:
        doc.components.append(
            Component(
                kind="connection",
                identifier=str(conn.get("identifier") or conn.get("id") or ""),
                name=str(conn.get("name") or ""),
                relationships=[str(r) for r in (conn.get("selectedRelationships") or [])],
                group_path=group_path,
                raw=conn,
            )
        )
    for key, label in (("inputPorts", "INPUT_PORT"), ("outputPorts", "OUTPUT_PORT")):
        for port in node.get(key) or []:
            doc.components.append(
                Component(
                    kind="port",
                    identifier=str(port.get("identifier") or ""),
                    name=str(port.get("name") or ""),
                    type_name=label,
                    group_path=group_path,
                    raw=port,
                )
            )
    for funnel in node.get("funnels") or []:
        doc.components.append(
            Component(
                kind="funnel",
                identifier=str(funnel.get("identifier") or ""),
                name="funnel",
                group_path=group_path,
                raw=funnel,
            )
        )
    for rpg in node.get("remoteProcessGroups") or []:
        doc.components.append(
            Component(
                kind="rpg",
                identifier=str(rpg.get("identifier") or ""),
                name=str(rpg.get("name") or rpg.get("targetUri") or "remote"),
                group_path=group_path,
                raw=rpg,
            )
        )

    # 1.x carries group Variables; 2.x does not. Capture whatever is present so
    # the engine can warn about ${var} references that will break on 2.x.
    variables = node.get("variables")
    if isinstance(variables, dict):
        for name, value in variables.items():
            doc.variables.append({"name": name, "value": value, "groupPath": group_path})

    for child in node.get("processGroups") or []:
        name = child.get("name") or "group"
        _walk_json_group(child, doc, f"{group_path}{name}/")


def _json_processor(node: dict[str, Any], group_path: str) -> Component:
    # 1.x nests config under "properties" directly on the node; both lines use
    # "properties", but 2.x adds "propertyDescriptors" alongside.
    props = node.get("properties")
    if not isinstance(props, dict):
        props = {}
    return Component(
        kind="processor",
        identifier=str(node.get("identifier") or node.get("id") or ""),
        name=str(node.get("name") or ""),
        type_name=str(node.get("type") or ""),
        bundle=dict(node.get("bundle") or {}),
        properties=dict(props),
        scheduling_strategy=str(node.get("schedulingStrategy") or ""),
        scheduling_period=str(node.get("schedulingPeriod") or ""),
        auto_terminated=[str(r) for r in (node.get("autoTerminatedRelationships") or [])],
        group_path=group_path,
        raw=node,
    )


def _json_service(node: dict[str, Any], group_path: str) -> Component:
    props = node.get("properties")
    if not isinstance(props, dict):
        props = {}
    return Component(
        kind="controllerService",
        identifier=str(node.get("identifier") or node.get("id") or ""),
        name=str(node.get("name") or ""),
        type_name=str(node.get("type") or ""),
        bundle=dict(node.get("bundle") or {}),
        properties=dict(props),
        group_path=group_path,
        raw=node,
    )


def _json_parameter_contexts(data: dict[str, Any], root: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    # 2.x puts them at the snapshot level; 1.x nests one on the group.
    raw = data.get("parameterContexts")
    if isinstance(raw, dict):
        for name, ctx in raw.items():
            contexts.append(
                {
                    "name": ctx.get("name") or name,
                    "parameters": [
                        p.get("name") for p in (ctx.get("parameters") or []) if isinstance(p, dict)
                    ],
                }
            )
    elif isinstance(raw, list):
        for ctx in raw:
            if isinstance(ctx, dict):
                contexts.append(
                    {
                        "name": ctx.get("name"),
                        "parameters": [
                            p.get("name") for p in (ctx.get("parameters") or []) if isinstance(p, dict)
                        ],
                    }
                )
    inline = root.get("parameterContextName")
    if inline and not contexts:
        contexts.append({"name": inline, "parameters": []})
    return contexts


def _parse_studio_spec(data: dict[str, Any], text: str, filename: str) -> FlowDocument:
    doc = FlowDocument(
        source_format=FORMAT_STUDIO_SPEC,
        filename=filename,
        original=data,
        raw_text=text,
    )
    doc.root_name = str(data.get("processGroupName") or "flow")

    def walk(group: dict[str, Any], path: str) -> None:
        for proc in group.get("processors") or []:
            doc.components.append(
                Component(
                    kind="processor",
                    identifier=str(proc.get("name") or ""),
                    name=str(proc.get("name") or ""),
                    type_name=str(proc.get("type") or ""),
                    bundle=dict(proc.get("bundle") or {}),
                    properties=dict(proc.get("properties") or {}),
                    scheduling_strategy=str(proc.get("schedulingStrategy") or ""),
                    scheduling_period=str(proc.get("schedulingPeriod") or ""),
                    auto_terminated=[str(r) for r in (proc.get("autoTerminated") or [])],
                    group_path=path,
                    raw=proc,
                )
            )
        for svc in group.get("controllerServices") or []:
            doc.components.append(
                Component(
                    kind="controllerService",
                    identifier=str(svc.get("name") or ""),
                    name=str(svc.get("name") or ""),
                    type_name=str(svc.get("type") or ""),
                    properties=dict(svc.get("properties") or {}),
                    group_path=path,
                    raw=svc,
                )
            )
        for conn in group.get("connections") or []:
            doc.components.append(
                Component(
                    kind="connection",
                    identifier=f"{conn.get('from')}->{conn.get('to')}",
                    name=str(conn.get("name") or ""),
                    relationships=[str(r) for r in (conn.get("relationships") or [])],
                    group_path=path,
                    raw=conn,
                )
            )
        for child in group.get("processGroups") or []:
            walk(child, f"{path}{child.get('processGroupName') or child.get('name') or 'group'}/")

    walk(data, "/")
    for ctx in data.get("parameterContexts") or []:
        doc.parameter_contexts.append(
            {
                "name": ctx.get("name"),
                "parameters": [p.get("name") for p in (ctx.get("parameters") or [])],
            }
        )
    doc.detected_version = str(data.get("nifiVersion") or "") or None
    doc.detection_confidence = CONFIDENCE_CERTAIN if doc.detected_version else CONFIDENCE_UNKNOWN
    if doc.detected_version:
        doc.detection_evidence.append(
            f"Flow Studio spec declares nifiVersion={doc.detected_version}."
        )
    return doc


# --- Source-version detection -------------------------------------------------
#
# The single most reliable signal is the NAR bundle version stamped on every
# component: NiFi writes the running instance's version there. Structural
# markers are the fallback.

_SEMVER = re.compile(r"^\d+\.\d+")


def _detect_version_xml(root: ET.Element, doc: FlowDocument) -> None:
    versions = _bundle_versions(doc)
    if versions:
        best = _most_common(versions)
        doc.detected_version = best
        doc.detection_confidence = CONFIDENCE_CERTAIN
        doc.detection_evidence.append(
            f"NAR bundle version {best} is stamped on {versions.count(best)} of "
            f"{len(versions)} components."
        )
    else:
        # Templates always come from 1.x — 2.0 removed the feature — so even with
        # no bundle stamps we know the line, just not the exact release.
        doc.detected_version = None
        doc.detection_confidence = CONFIDENCE_UNKNOWN
        doc.detection_evidence.append(
            "No NAR bundle versions found in the template. XML templates are a "
            "NiFi 1.x-only feature (removed in 2.0), so the source is 1.x — "
            "select the exact release manually."
        )

    encoding = root.get("encoding-version") or root.get("encodingVersion")
    if encoding:
        doc.detection_evidence.append(f"Template encoding-version={encoding}.")


def _detect_version_json(data: dict[str, Any], doc: FlowDocument) -> None:
    versions = _bundle_versions(doc)
    if versions:
        best = _most_common(versions)
        doc.detected_version = best
        doc.detection_confidence = CONFIDENCE_CERTAIN
        doc.detection_evidence.append(
            f"NAR bundle version {best} is stamped on {versions.count(best)} of "
            f"{len(versions)} components."
        )
        return

    # No bundle stamps: fall back to structural markers that differ by line.
    evidence: list[str] = []
    line_guess: int | None = None

    text = doc.raw_text
    if '"executionEngine"' in text or '"statelessFlowTimeout"' in text:
        line_guess = 2
        evidence.append(
            "Flow contains 'executionEngine' / 'statelessFlowTimeout', which only "
            "NiFi 2.x writes."
        )
    if '"EVENT_DRIVEN"' in text:
        line_guess = 1
        evidence.append(
            "Flow uses EVENT_DRIVEN scheduling, which was removed in NiFi 2.0, so "
            "the source is 1.x."
        )
    root = _json_root_group(data) or {}
    if isinstance(root.get("variables"), dict) and root.get("variables"):
        line_guess = 1
        evidence.append(
            "Flow declares group Variables, which the Variable Registry provided "
            "in 1.x only (removed in 2.0)."
        )

    doc.detection_evidence.extend(evidence)
    if line_guess == 2:
        doc.detected_version = None
        doc.detection_confidence = CONFIDENCE_LIKELY
        doc.detection_evidence.append("Source line is 2.x; select the exact release manually.")
    elif line_guess == 1:
        doc.detected_version = None
        doc.detection_confidence = CONFIDENCE_LIKELY
        doc.detection_evidence.append("Source line is 1.x; select the exact release manually.")
    else:
        doc.detected_version = None
        doc.detection_confidence = CONFIDENCE_UNKNOWN
        doc.detection_evidence.append(
            "No version markers found. A JSON flow definition is produced by both "
            "NiFi 1.x and 2.x, so the source version cannot be inferred from the "
            "file alone — please select it."
        )


def _bundle_versions(doc: FlowDocument) -> list[str]:
    out: list[str] = []
    for comp in doc.components:
        version = comp.bundle_version
        if version and _SEMVER.match(version):
            out.append(version)
    return out


def _most_common(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], parse_version(kv[0])))[0][0]


def detected_line(doc: FlowDocument) -> str | None:
    """"1.x"/"2.x" if the parse could establish the line, else None."""
    if doc.detected_version:
        return f"{parse_version(doc.detected_version)[0]}.x"
    for note in doc.detection_evidence:
        if "line is 2.x" in note:
            return "2.x"
        if "line is 1.x" in note or "source is 1.x" in note:
            return "1.x"
    if doc.source_format == FORMAT_XML_TEMPLATE:
        return "1.x"
    return None
