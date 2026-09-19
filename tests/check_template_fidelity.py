"""Compare a converted flow definition against the XML template it came from.

Not part of the pytest suite (no `test_` prefix): point it at any real template
to confirm the migrated JSON still describes the same canvas.

    python tests/check_template_fidelity.py path/to/template.xml [target_version]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flow_document import parse_flow_file  # noqa: E402
from flow_schema import xml_children  # noqa: E402
from migration_engine import analyze_migration, generate_migrated_flow  # noqa: E402


def xml_components(node: ET.Element, out: dict[str, list] | None = None) -> dict[str, list]:
    out = out if out is not None else {"processors": [], "connections": [], "funnels": []}
    for proc in xml_children(node, "processors"):
        config = proc.find("config")
        props = {}
        for entry in (config.findall("properties/entry") if config is not None else []):
            key = entry.find("key")
            value = entry.find("value")
            if key is not None and key.text:
                props[key.text] = value.text if value is not None else None
        out["processors"].append(
            {
                "id": text(proc, "id"),
                "name": text(proc, "name"),
                "type": text(proc, "type"),
                "properties": props,
            }
        )
    for conn in xml_children(node, "connections"):
        out["connections"].append(
            {
                "id": text(conn, "id"),
                "source": text(conn.find("source"), "id"),
                "destination": text(conn.find("destination"), "id"),
            }
        )
    for funnel in xml_children(node, "funnels"):
        out["funnels"].append({"id": text(funnel, "id")})
    for group in xml_children(node, "processGroups"):
        contents = group.find("contents")
        xml_components(contents if contents is not None else group, out)
    return out


def json_components(group: dict, out: dict[str, list] | None = None) -> dict[str, list]:
    out = out if out is not None else {"processors": [], "connections": [], "funnels": []}
    for proc in group.get("processors") or []:
        out["processors"].append(
            {
                "id": proc["identifier"],
                "name": proc["name"],
                "type": proc["type"],
                "properties": proc.get("properties") or {},
            }
        )
    for conn in group.get("connections") or []:
        out["connections"].append(
            {
                "id": conn["identifier"],
                "source": conn["source"]["id"],
                "destination": conn["destination"]["id"],
            }
        )
    for funnel in group.get("funnels") or []:
        out["funnels"].append({"id": funnel["identifier"]})
    for child in group.get("processGroups") or []:
        json_components(child, out)
    return out


def text(node: ET.Element | None, child: str) -> str:
    if node is None:
        return ""
    found = node.find(child)
    return (found.text or "").strip() if found is not None and found.text else ""


def walk_groups(group: dict, out: list | None = None) -> list:
    out = out if out is not None else []
    out.append(group)
    for child in group.get("processGroups") or []:
        walk_groups(child, out)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else "2.6.0"

    xml_text = path.read_text(encoding="utf-8")
    root = ET.fromstring(xml_text)
    snippet = root.find("snippet")
    source = xml_components(snippet if snippet is not None else root)

    doc = parse_flow_file(xml_text, path.name)
    analysis = analyze_migration(doc, doc.detected_version or "1.25.0", target)
    migrated = generate_migrated_flow(doc, analysis).migrated
    produced = json_components(migrated["flowContents"])

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" - {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    for kind in ("processors", "connections", "funnels"):
        check(
            f"{kind} count",
            len(source[kind]) == len(produced[kind]),
            f"xml={len(source[kind])} json={len(produced[kind])}",
        )

    by_id = {p["id"]: p for p in produced["processors"]}
    for proc in source["processors"]:
        out = by_id.get(proc["id"])
        if out is None:
            check(f"processor {proc['name']} survives", False, "missing from JSON")
            continue
        check(f"processor {proc['name']} keeps its name", out["name"] == proc["name"])
        check(f"processor {proc['name']} keeps its type", out["type"] == proc["type"])
        missing = [k for k in proc["properties"] if k not in out["properties"]]
        check(f"processor {proc['name']} keeps every property", not missing, str(missing))
        differing = [
            k
            for k, v in proc["properties"].items()
            if k in out["properties"] and (out["properties"][k] or None) != (v or None)
        ]
        check(f"processor {proc['name']} keeps property values", not differing, str(differing))

    conn_by_id = {c["id"]: c for c in produced["connections"]}
    for conn in source["connections"]:
        out = conn_by_id.get(conn["id"])
        if out is None:
            check(f"connection {conn['id'][:8]} survives", False, "missing from JSON")
            continue
        check(
            f"connection {conn['id'][:8]} keeps its endpoints",
            out["source"] == conn["source"] and out["destination"] == conn["destination"],
            f"{out['source'][:8]} to {out['destination'][:8]}",
        )

    ids = {p["id"] for p in produced["processors"]}
    ids |= {f["id"] for f in produced["funnels"]}
    for group in walk_groups(migrated["flowContents"]):
        ids.add(group["identifier"])
        for key in ("inputPorts", "outputPorts"):
            ids |= {p["identifier"] for p in group.get(key) or []}
    dangling = [
        c["identifier"]
        for group in walk_groups(migrated["flowContents"])
        for c in group.get("connections") or []
        if c["source"]["id"] not in ids or c["destination"]["id"] not in ids
    ]
    check("no connection points at a missing component", not dangling, str(dangling[:3]))

    group_ids = {g["identifier"] for g in walk_groups(migrated["flowContents"])}
    orphans = [
        proc["identifier"]
        for group in walk_groups(migrated["flowContents"])
        for proc in group.get("processors") or []
        if proc.get("groupIdentifier") not in group_ids
    ]
    check("every component belongs to a group in the file", not orphans, str(orphans[:3]))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed.")
        return 1
    print(f"All fidelity checks passed for {path.name} -> NiFi {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
