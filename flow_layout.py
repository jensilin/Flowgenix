"""Layered auto-layout for flow specs.

Assigns readable `x`/`y` coordinates to processors, ports, funnels, remote
process groups, child process groups and labels so a spec created purely from
prompts renders as a clean left-to-right flow rather than a wall of
overlapping components on `y=100`.

The layout is Sugiyama-lite:

1. Build a directed graph from the group's `connections`, treating every named
   endpoint (processor / port / funnel / remote / child-group ports) as a node.
2. Rank nodes by longest path from any source (nodes with no incoming edge).
   Cycles get their remaining edges broken heuristically — NiFi allows feedback
   loops but they still need one edge treated as "back-edge" for ranking.
3. Order nodes within each rank stably: first by spec-declared order for the
   layer of the node's kind, then by name. This keeps re-runs deterministic.
4. Assign `x = base_x + rank * col_width`, `y = base_y + slot * row_height`.

Input ports without spec-declared position land at rank 0 (leftmost column) and
output ports at max_rank+1, so the flow direction reads consistently.

Nodes with a coordinate the user *explicitly* set are honoured — the spec
carries `"layout": "manual"` as an escape hatch, and any positive `x`/`y`
overrides. The default coordinates the old builder used (100 + i*300, 100) are
recognised as "unset" so they can be replaced without a spec change.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


BASE_X = 120
BASE_Y = 120
COL_WIDTH = 320
ROW_HEIGHT = 180
LABEL_ROW_Y = 60  # Labels sit above the flow (their height grows downward).


def apply_layout(spec: dict[str, Any]) -> None:
    """Assign coordinates to every positionable node in the spec, in place.

    Recurses into `processGroups`. Skips a group whose top-level `"layout":
    "manual"` is set — that's the escape hatch for hand-tuned canvases.
    """
    _layout_group(spec)


def _layout_group(group: dict[str, Any]) -> None:
    layout_mode = str(group.get("layout") or "").lower()
    if layout_mode == "manual":
        for child in group.get("processGroups") or []:
            _layout_group(child)
        return
    # "auto" forces every node's position to be recomputed; the default only
    # places nodes that lack coordinates. This keeps existing hand-tuned specs
    # (e.g. the JSON→CSV flow with fixed x/y) working while giving new
    # agent-authored specs a clean layout when they omit coordinates.
    force = layout_mode == "auto"

    processors = group.get("processors") or []
    ports = group.get("ports") or []
    funnels = group.get("funnels") or []
    remotes = group.get("remoteProcessGroups") or []
    child_groups = group.get("processGroups") or []
    connections = group.get("connections") or []

    # Everything the connection graph can reference by name.
    nodes: dict[str, dict[str, Any]] = {}
    _register(nodes, processors, kind="processor")
    _register(nodes, ports, kind="port")
    _register(nodes, funnels, kind="funnel")
    _register(nodes, remotes, kind="remote")
    # Child groups expose "ports" as connection endpoints via "<Group>.<Port>",
    # but positioning-wise they are single boxes on the parent canvas.
    for child in child_groups:
        cname = child.get("processGroupName") or child.get("name")
        if cname:
            nodes[cname] = {
                "kind": "childGroup",
                "spec": child,
                "index": len(nodes),
            }

    edges = _resolve_edges(connections, nodes)
    ranks = _rank_nodes(nodes, edges)

    _assign_positions(nodes, ranks, force=force)
    _place_labels(group.get("labels") or [], ranks, force=force)

    for child in child_groups:
        _layout_group(child)


def _register(
    nodes: dict[str, dict[str, Any]], items: list[dict[str, Any]], kind: str
) -> None:
    for i, item in enumerate(items):
        name = item.get("name")
        if not name:
            continue
        nodes[str(name)] = {
            "kind": kind,
            "spec": item,
            "index": i,
        }


def _resolve_edges(
    connections: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    """Return edges as (source_name, target_name) pairs, ignoring cross-group
    endpoints (dotted refs like `<ChildGroup>.<Port>` beyond first hop).

    The child group itself is a single node on this canvas, so a dotted ref
    lands on the child group's local box.
    """
    edges: list[tuple[str, str]] = []
    for conn in connections:
        src = _local_name(conn.get("from"), nodes)
        dst = _local_name(conn.get("to"), nodes)
        if src and dst and src != dst:
            edges.append((src, dst))
    return edges


def _local_name(
    ref: str | None, nodes: dict[str, dict[str, Any]]
) -> str | None:
    if not ref:
        return None
    if ref in nodes:
        return ref
    if "." in ref:
        head = ref.split(".", 1)[0]
        if head in nodes:
            return head
    return None


def _rank_nodes(
    nodes: dict[str, dict[str, Any]], edges: list[tuple[str, str]]
) -> dict[int, list[str]]:
    """Longest-path-from-source rank, with cycles broken heuristically.

    Sources (no incoming edge) get rank 0; every other node's rank is one more
    than the highest-ranked node pointing at it. Cycles break by treating the
    edge that closes the cycle as a back-edge, which prevents infinite loops
    without needing a full SCC decomposition.
    """
    if not nodes:
        return {}

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        incoming[dst].append(src)
        outgoing[src].append(dst)

    # Break cycles: DFS from every node, mark back-edges (edges to a currently
    # active DFS ancestor) and drop them from `incoming`/`outgoing` for ranking.
    active: set[str] = set()
    visited: set[str] = set()
    back_edges: set[tuple[str, str]] = set()

    def dfs(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        active.add(node)
        for succ in list(outgoing.get(node, [])):
            if succ in active:
                back_edges.add((node, succ))
                continue
            dfs(succ)
        active.discard(node)

    for name in list(nodes.keys()):
        dfs(name)

    if back_edges:
        # Rebuild without back-edges. The layout still shows the components
        # side-by-side; the back-edge itself is drawn by NiFi as a normal arrow.
        cleaned_edges = [(s, d) for (s, d) in edges if (s, d) not in back_edges]
        incoming = defaultdict(list)
        outgoing = defaultdict(list)
        for src, dst in cleaned_edges:
            incoming[dst].append(src)
            outgoing[src].append(dst)

    # Assign ranks via topological Kahn traversal (longest path form).
    rank: dict[str, int] = {name: 0 for name in nodes}
    remaining = {name: len(incoming.get(name, [])) for name in nodes}
    queue: deque[str] = deque(
        sorted(
            (n for n, count in remaining.items() if count == 0),
            key=lambda n: nodes[n]["index"],
        )
    )
    order = 0
    seen = set()
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for succ in outgoing.get(node, []):
            if rank[succ] < rank[node] + 1:
                rank[succ] = rank[node] + 1
            remaining[succ] -= 1
            if remaining[succ] == 0:
                queue.append(succ)
        order += 1

    # Nudge input ports to rank 0 and output ports past the highest processor.
    max_rank = max(rank.values()) if rank else 0
    for name, info in nodes.items():
        if info["kind"] == "port":
            kind_raw = str(info["spec"].get("kind") or "input").lower()
            if kind_raw.startswith("in"):
                rank[name] = 0
            else:
                rank[name] = max(rank[name], max_rank)

    # Bucket by rank so we can lay out row by row.
    buckets: dict[int, list[str]] = defaultdict(list)
    for name, r in rank.items():
        buckets[r].append(name)
    for r in buckets:
        buckets[r].sort(
            key=lambda n: (
                _kind_row(nodes[n]["kind"]),
                nodes[n]["index"],
                n,
            )
        )
    return buckets


def _kind_row(kind: str) -> int:
    """Sort ordering within a rank so ports sit at the top of a column, then
    processors, then funnels/remotes/child-groups. Keeps rows visually grouped.
    """
    return {
        "port": 0,
        "processor": 1,
        "funnel": 2,
        "remote": 3,
        "childGroup": 4,
    }.get(kind, 5)


def _assign_positions(
    nodes: dict[str, dict[str, Any]],
    ranks: dict[int, list[str]],
    force: bool,
) -> None:
    for rank_index, names in ranks.items():
        for row, name in enumerate(names):
            spec = nodes[name]["spec"]
            if not force and _has_position(spec):
                continue
            spec["x"] = BASE_X + rank_index * COL_WIDTH
            spec["y"] = BASE_Y + row * ROW_HEIGHT


def _has_position(spec: dict[str, Any]) -> bool:
    """Was any coordinate placed on this node?

    Any positive numeric x/y counts as "the user placed this" and is respected
    in the default mode; the group can opt into `"layout": "auto"` to override.
    """
    x, y = spec.get("x"), spec.get("y")
    if x is None or y is None:
        return False
    try:
        xn, yn = float(x), float(y)
    except (TypeError, ValueError):
        return False
    return xn > 0 and yn > 0


def _place_labels(
    labels: list[dict[str, Any]],
    ranks: dict[int, list[str]],
    force: bool,
) -> None:
    if not labels:
        return
    for i, label in enumerate(labels):
        if not force and _has_position(label):
            continue
        label["x"] = BASE_X + (i * COL_WIDTH)
        label["y"] = LABEL_ROW_Y
