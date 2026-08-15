#!/usr/bin/env python3
"""Summarize ADMTune experiment_metrics.json files into CSV/JSON."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDS = (
    "variant",
    "record_dir",
    "best_perf",
    "T_opt",
    "PIE",
    "N_exec",
    "N_target",
    "R_filter",
    "llm_calls",
    "tokens",
    "stop_reason",
    "gpr_MAE",
    "gpr_RMSE",
    "gpr_R2",
)


def load_metrics(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    gpr = data.get("gpr") or {}
    return {
        "variant": data.get("variant", path.parent.name),
        "record_dir": str(path.parent),
        "best_perf": data.get("best_perf"),
        "T_opt": data.get("T_opt"),
        "PIE": data.get("PIE"),
        "N_exec": data.get("N_exec"),
        "N_target": data.get("N_target"),
        "R_filter": data.get("R_filter"),
        "llm_calls": data.get("llm_calls"),
        "tokens": data.get("tokens"),
        "stop_reason": data.get("stop_reason"),
        "gpr_MAE": gpr.get("MAE"),
        "gpr_RMSE": gpr.get("RMSE"),
        "gpr_R2": gpr.get("R2"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path, help="experiment root dirs")
    parser.add_argument("--out", type=Path, default=Path("admtune_metrics.csv"))
    args = parser.parse_args()

    rows = []
    for root in args.roots:
        for path in root.rglob("experiment_metrics.json"):
            rows.append(load_metrics(path))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    json_path = args.out.with_suffix(".json")
    json_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows)} csv={args.out} json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
