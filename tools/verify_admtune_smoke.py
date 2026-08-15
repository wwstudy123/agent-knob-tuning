#!/usr/bin/env python3
"""Offline ADMTune smoke checks (no MySQL / no LLM required)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "configuration recommender"
sys.path.insert(0, str(REC))

from admtune.metrics import ExperimentLedger, compute_gpr_regression_metrics
from admtune.decision import decide_admtune, map_legacy_action
from admtune.prescreen import three_d_prescreen
from admtune.reflection_policy import ReflectionPolicy, TriggerContext, build_delta_m
from admtune.action_executor import ActionExecutor


def main() -> int:
    failures = []

    # 1) metrics ledger
    ledger = ExperimentLedger()
    ledger.set_baseline(10.0)
    ledger.record_prescreen(10, 2)
    ledger.record_execution(12.0, success=True, prediction={"mean": 11.0, "std": 1.0, "confidence": 0.8})
    ledger.record_execution(9.0, success=True, prediction={"mean": 10.0, "std": 2.0, "confidence": 0.4})
    ledger.record_llm(2, 100)
    summary = ledger.summary()
    if summary["N_exec"] != 2 or abs(summary["R_filter"] - 0.8) > 1e-9:
        failures.append(f"ledger bad: {summary}")
    if summary["best_perf"] < 12.0:
        failures.append("best_perf")

    gpr = compute_gpr_regression_metrics(ledger.gpr_pairs)
    if gpr["overall"]["n"] != 2 or gpr["overall"]["MAE"] is None:
        failures.append(f"gpr metrics: {gpr}")

    # 2) decision mapping
    if map_legacy_action("reselect_knobs") != "Reselect":
        failures.append("map reselect")
    action, _ = decide_admtune({"failure_rate": 0.5}, enabled=True)
    if action != "AdjustRange":
        failures.append(f"decide fail_rate -> {action}")
    action, _ = decide_admtune({}, enabled=False)
    if action != "Execute":
        failures.append("no_decision ablation")

    # 3) 3D prescreen
    ranked = []
    for i in range(6):
        ranked.append(
            {
                "config": {"knob1": i},
                "prediction": {
                    "mean": 10 + i,
                    "std": 0.5 + 0.2 * i,
                    "failure_probability": 0.1 * i,
                    "success_probability": 1 - 0.1 * i,
                },
                "feasible": i < 5,
                "acquisition": float(i),
            }
        )
    screen = three_d_prescreen(ranked, top_k=2, risk_threshold=0.75, conf_threshold=0.2)
    if screen.n_target != 6 or screen.n_accepted < 1:
        failures.append(f"prescreen: {screen.to_dict()}")

    # 4) reflection modes
    pol = ReflectionPolicy("conditional_delta")
    if pol.trigger(TriggerContext(action="Execute", improve_delta=0.0)):
        failures.append("conditional should not trigger on quiet Execute")
    if not pol.trigger(TriggerContext(action="Reselect")):
        failures.append("conditional should trigger on Reselect")
    delta = build_delta_m(knobs_now={"a": 2}, knobs_prev={"a": 1}, score_now=1.1, score_prev_best=1.0)
    pack = pol.prompt_history(
        triggered=True,
        full_history_text="FULL",
        delta_m=delta,
        compact_reflection="COMPACT",
    )
    if "Delta_M_t" not in pack["history_text"]:
        failures.append("delta prompt missing")

    # 5) action executor nudge (temp pruned file)
    with tempfile.TemporaryDirectory() as tmp:
        pruned = Path(tmp) / "pruned.json"
        pruned.write_text(
            json.dumps(
                {
                    "knob1": {"type": "integer", "min_value": 100, "max_value": 200},
                    "knob2": {"type": "enum", "enum_values": ["ON", "OFF"]},
                }
            ),
            encoding="utf-8",
        )
        ex = ActionExecutor(pruned_knobs_path=str(pruned), record_dir=tmp)
        result = ex.apply("AdjustRange", "elevated_failure_rate")
        if not result.applied or result.artifacts.get("changed", {}).get("updated", 0) < 1:
            failures.append(f"adjust range: {result.to_dict()}")

    out_dir = ROOT / "configuration recommender" / "record-admtune-offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger.write_json(out_dir / "experiment_metrics.json", extra={"variant": "offline_smoke"})
    (out_dir / "gpr_metrics.json").write_text(json.dumps(gpr, indent=2) + "\n", encoding="utf-8")

    if failures:
        print("FAIL:")
        for item in failures:
            print(" -", item)
        return 1
    print("OK: ADMTune offline smoke passed")
    print(f"wrote {out_dir / 'experiment_metrics.json'}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
