"""A uniform, read-only view of a source flow for the readiness, preview and
fidelity (test-only) passes.

XML templates and JSON flow definitions are both turned into the same
VersionedProcessGroup shape (`flowContents`), so each pass is written once.
Nothing here modifies the document or the migrated output.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from flow_document import FORMAT_XML_TEMPLATE, FlowDocument
from flow_schema import xml_children
from versioned_flow import snapshot_from_xml_template

# Used only to stamp bundles while reshaping an XML template for inspection.
_INSPECTION_VERSION = "1.28.1"


def source_snapshot(doc: FlowDocument) -> dict[str, Any]:
    """The source flow as a VersionedFlowSnapshot-like dict, unmigrated."""
    if doc.source_format == FORMAT_XML_TEMPLATE and doc.xml_root is not None:
        return snapshot_from_xml_template(doc.xml_root, doc.detected_version or _INSPECTION_VERSION)
    data = doc.original if isinstance(doc.original, dict) else {}
    for key in ("flowContents", "rootGroup"):
        if isinstance(data.get(key), dict):
            return {**data, "flowContents": data[key]}
    if data.get("componentType") == "PROCESS_GROUP":
        return {"flowContents": data}
    return {"flowContents": {}}


def root_group(snapshot: dict[str, Any]) -> dict[str, Any]:
    group = snapshot.get("flowContents")
    return group if isinstance(group, dict) else {}


def walk_groups(group: dict[str, Any], path: str = "/") -> Iterator[tuple[dict[str, Any], str]]:
    """Yield every process group with its path, root first."""
    yield group, path
    for child in group.get("processGroups") or []:
        if isinstance(child, dict):
            yield from walk_groups(child, f"{path}{child.get('name') or 'group'}/")


def items(group: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [node for node in group.get(key) or [] if isinstance(node, dict)]


def component_id(node: dict[str, Any]) -> str:
    return str(node.get("identifier") or node.get("id") or "")


def declared_relationships(doc: FlowDocument) -> dict[str, list[str]]:
    """Processor id -> every relationship the processor offers.

    Only XML templates carry this (`<relationships>` per processor). A JSON flow
    definition records just the auto-terminated set, so the answer is unknown
    there and the processor is absent from the map.
    """
    out: dict[str, list[str]] = {}
    if doc.source_format != FORMAT_XML_TEMPLATE or doc.xml_root is None:
        return out
    for node in doc.xml_root.iter():
        if node.tag.rsplit("}", 1)[-1] not in ("processors", "processor"):
            continue
        proc_id = (node.findtext("id") or "").strip()
        names = [
            (rel.findtext("name") or "").strip()
            for rel in xml_children(node, "relationships")
        ]
        names = [name for name in names if name]
        if proc_id and names:
            out[proc_id] = names
    return out
