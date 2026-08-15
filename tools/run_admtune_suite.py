#!/usr/bin/env python3
"""Generate small-budget ADMTune ablation configs (baselines skipped)."""

from __future__ import annotations

import argparse
import configparser
import shutil
from pathlib import Path


VARIANTS = {
    "admtune_full": {
        "adaptive_controller_enabled": "true",
        "surrogate_enabled": "true",
        "reflection_enabled": "true",
        "prescreen_enabled": "true",
        "reflection_mode": "conditional_delta",
    },
    "no_decision": {
        "adaptive_controller_enabled": "false",
        "surrogate_enabled": "true",
        "reflection_enabled": "true",
        "prescreen_enabled": "true",
        "reflection_mode": "conditional_delta",
    },
    "no_prescreen": {
        "adaptive_controller_enabled": "true",
        "surrogate_enabled": "false",
        "reflection_enabled": "true",
        "prescreen_enabled": "false",
        "reflection_mode": "conditional_delta",
    },
    "no_reflection": {
        "adaptive_controller_enabled": "true",
        "surrogate_enabled": "true",
        "reflection_enabled": "false",
        "prescreen_enabled": "true",
        "reflection_mode": "off",
    },
    "reflect_full": {
        "adaptive_controller_enabled": "true",
        "surrogate_enabled": "true",
        "reflection_enabled": "true",
        "prescreen_enabled": "true",
        "reflection_mode": "full_history",
    },
    "reflect_delta_always": {
        "adaptive_controller_enabled": "true",
        "surrogate_enabled": "true",
        "reflection_enabled": "true",
        "prescreen_enabled": "true",
        "reflection_mode": "unconstrained_delta",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("config.admtune.smoke.ini"),
        help="base smoke config",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/admtune-smoke-configs"))
    parser.add_argument("--n-exec-max", type=int, default=5)
    parser.add_argument(
        "--variants",
        default=",".join(VARIANTS),
        help="comma-separated variant names",
    )
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in variants:
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant: {name}")
        cfg = configparser.ConfigParser()
        if not cfg.read(args.base, encoding="utf-8"):
            raise SystemExit(f"cannot read {args.base}")
        flags = VARIANTS[name]
        section = cfg["configuration recommender"]
        section["adaptive_controller_enabled"] = flags["adaptive_controller_enabled"]
        section["surrogate_enabled"] = flags["surrogate_enabled"]
        section["reflection_enabled"] = flags["reflection_enabled"]
        section["benchmark_budget"] = str(args.n_exec_max)
        section["iteration"] = str(max(1, min(3, args.n_exec_max)))
        section["record_dir"] = f"record-admtune-{name}"
        section["run_mode"] = "fresh"
        if "admtune" not in cfg:
            cfg.add_section("admtune")
        adm = cfg["admtune"]
        adm["enabled"] = "true"
        adm["n_exec_max"] = str(args.n_exec_max)
        adm["prescreen_enabled"] = flags["prescreen_enabled"]
        adm["reflection_mode"] = flags["reflection_mode"]
        adm["variant"] = name
        dest = args.out_dir / f"config.{name}.ini"
        with dest.open("w", encoding="utf-8") as handle:
            cfg.write(handle)
        print(f"wrote {dest}")

    readme = args.out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# ADMTune smoke configs",
                "",
                "Baselines are skipped in this pass.",
                "",
                "Offline check:",
                "```bash",
                "python tools/verify_admtune_smoke.py",
                "```",
                "",
                "Online (needs MySQL + LLM_server):",
                "```bash",
                "export AGENTTUNE_CONFIG=experiments/admtune-smoke-configs/config.admtune_full.ini",
                "# start LLM_server with same AGENTTUNE_CONFIG",
                "python configuration\\ recommender/DB_client.py",
                "python tools/summarize_admtune.py configuration\\ recommender --out experiments/admtune_metrics.csv",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"wrote {readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
