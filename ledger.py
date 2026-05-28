from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_LEDGER_PATH = "cost_ledger.jsonl"


def _path(path: str | None) -> str:
    return path or os.environ.get("KOSHR_LEDGER") or DEFAULT_LEDGER_PATH


def record(cost_obj, command: str, brain: str, path: str | None = None) -> None:
    c = cost_obj
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "brain": brain,
        "command": command,
        "model": c.model if c else None,
        "input_tokens": c.input_tokens if c else 0,
        "output_tokens": c.output_tokens if c else 0,
        "cache_write_tokens": c.cache_write_tokens if c else 0,
        "cache_read_tokens": c.cache_read_tokens if c else 0,
        "total": c.total if c else 0.0,
        "cache_savings": c.cache_savings if c else 0.0,
    }
    with open(_path(path), "a") as f:
        f.write(json.dumps(entry) + "\n")


def summarize(path: str | None = None) -> dict:
    p = _path(path)
    out = {"requests": 0, "total_cost": 0.0, "avg_cost": 0.0,
           "cache_savings": 0.0, "by_brain": {}, "skipped": 0}
    if not os.path.exists(p):
        return out
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                out["skipped"] += 1
                continue
            out["requests"] += 1
            out["total_cost"] += e.get("total", 0.0) or 0.0
            out["cache_savings"] += e.get("cache_savings", 0.0) or 0.0
            b = e.get("brain", "?")
            out["by_brain"][b] = out["by_brain"].get(b, 0) + 1
    if out["requests"]:
        out["avg_cost"] = out["total_cost"] / out["requests"]
    return out
