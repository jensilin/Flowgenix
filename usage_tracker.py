"""Per-model token accounting for Flow Studio.

Every agent run (flow builder or prompt assistant) reports the token usage the
Cursor SDK returns for it. Totals are kept in a small JSON file so the numbers
survive server restarts and are shared across browser tabs.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_STORE = REPO_ROOT / "data" / "token-usage.json"

COUNTERS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "reasoningTokens",
    "totalTokens",
)

_LOCK = threading.Lock()


def store_path() -> Path:
    """Where totals live. Override with FLOW_STUDIO_USAGE_FILE."""
    override = os.getenv("FLOW_STUDIO_USAGE_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_STORE


def usage_to_dict(usage: Any) -> dict[str, int] | None:
    """Normalize an SDK TokenUsage (or mapping) into plain ints."""
    if usage is None:
        return None

    def pick(*names: str) -> int | None:
        for name in names:
            if isinstance(usage, dict):
                if name in usage and usage[name] is not None:
                    return int(usage[name])
            else:
                value = getattr(usage, name, None)
                if value is not None:
                    return int(value)
        return None

    out = {
        "inputTokens": pick("input_tokens", "inputTokens") or 0,
        "outputTokens": pick("output_tokens", "outputTokens") or 0,
        "cacheReadTokens": pick("cache_read_tokens", "cacheReadTokens") or 0,
        "cacheWriteTokens": pick("cache_write_tokens", "cacheWriteTokens") or 0,
        "reasoningTokens": pick("reasoning_tokens", "reasoningTokens") or 0,
    }
    total = pick("total_tokens", "totalTokens")
    # The SDK's total excludes reasoning tokens (a subset of output).
    out["totalTokens"] = (
        total
        if total is not None
        else out["inputTokens"]
        + out["outputTokens"]
        + out["cacheReadTokens"]
        + out["cacheWriteTokens"]
    )
    return out


def cost_to_dict(cost: Any) -> dict[str, float] | None:
    if cost is None:
        return None

    def pick(*names: str) -> float | None:
        for name in names:
            if isinstance(cost, dict):
                if name in cost and cost[name] is not None:
                    return float(cost[name])
            else:
                value = getattr(cost, name, None)
                if value is not None:
                    return float(value)
        return None

    charged = pick("charged_cents", "chargedCents")
    raw = pick("raw_cost_cents", "rawCostCents")
    if charged is None and raw is None:
        return None
    return {"chargedCents": charged or 0.0, "rawCostCents": raw or 0.0}


def _empty_model_row(model: str) -> dict[str, Any]:
    row: dict[str, Any] = {"model": model, "runs": 0, "chargedCents": 0.0, "rawCostCents": 0.0}
    row.update({key: 0 for key in COUNTERS})
    row["sources"] = {}
    row["lastUsed"] = None
    return row


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": {}}
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return {"models": {}}
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp file so a crash mid-write cannot truncate the totals.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as handle:
        json.dump(data, handle, indent=2)
        temp = Path(handle.name)
    temp.replace(path)


def record(
    model: str,
    usage: Any,
    source: str = "flow",
    cost: Any = None,
) -> dict[str, int] | None:
    """Add one run's usage to the per-model totals. Returns the run's counts."""
    counts = usage_to_dict(usage)
    if counts is None:
        return None

    model_name = (model or "unknown").strip() or "unknown"
    costs = cost_to_dict(cost)
    path = store_path()

    with _LOCK:
        data = _load(path)
        models = data["models"]
        row = models.get(model_name) or _empty_model_row(model_name)
        row["runs"] = int(row.get("runs", 0)) + 1
        for key in COUNTERS:
            row[key] = int(row.get(key, 0)) + counts.get(key, 0)
        if costs:
            row["chargedCents"] = float(row.get("chargedCents", 0.0)) + costs["chargedCents"]
            row["rawCostCents"] = float(row.get("rawCostCents", 0.0)) + costs["rawCostCents"]
        sources = row.get("sources") or {}
        sources[source] = int(sources.get(source, 0)) + 1
        row["sources"] = sources
        row["lastUsed"] = time.time()
        models[model_name] = row
        data["updated"] = time.time()
        _save(path, data)

    return counts


def summary() -> dict[str, Any]:
    """Per-model rows plus a grand total, newest activity first."""
    data = _load(store_path())
    rows = list((data.get("models") or {}).values())
    rows.sort(key=lambda row: row.get("lastUsed") or 0, reverse=True)

    totals: dict[str, Any] = {"runs": 0, "chargedCents": 0.0, "rawCostCents": 0.0}
    totals.update({key: 0 for key in COUNTERS})
    for row in rows:
        totals["runs"] += int(row.get("runs", 0))
        totals["chargedCents"] += float(row.get("chargedCents", 0.0))
        totals["rawCostCents"] += float(row.get("rawCostCents", 0.0))
        for key in COUNTERS:
            totals[key] += int(row.get(key, 0))

    return {
        "models": rows,
        "totals": totals,
        "updated": data.get("updated"),
        "storePath": str(store_path()),
    }


def reset() -> None:
    with _LOCK:
        _save(store_path(), {"models": {}, "updated": time.time()})
