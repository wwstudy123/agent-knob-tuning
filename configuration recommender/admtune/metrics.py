"""Experiment metrics: best perf, T_opt, PIE, N_exec, N_target, R_filter."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass
class ExperimentLedger:
    """Track ADMTune paper metrics during one run."""

    y0: float = 0.0
    started_at: float = field(default_factory=time.time)
    n_exec: int = 0
    n_target: int = 0
    n_filtered: int = 0
    best_perf: float = 0.0
    t_opt: Optional[float] = None
    evals_to_opt95: Optional[int] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    gpr_pairs: List[Dict[str, Any]] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0
    actions: List[str] = field(default_factory=list)

    def set_baseline(self, y0: float) -> None:
        self.y0 = max(0.0, _finite(y0))
        if self.y0 > self.best_perf:
            self.best_perf = self.y0

    def record_prescreen(self, n_target: int, n_accepted: int) -> Dict[str, float]:
        n_target = max(0, int(n_target))
        n_accepted = max(0, min(int(n_accepted), n_target))
        filtered = n_target - n_accepted
        self.n_target += n_target
        self.n_filtered += filtered
        r_filter = (filtered / n_target) if n_target else 0.0
        return {
            "n_target": n_target,
            "n_accepted": n_accepted,
            "n_filtered": filtered,
            "R_filter": r_filter,
        }

    def record_execution(
        self,
        throughput: float,
        *,
        success: bool = True,
        prediction: Optional[Mapping[str, Any]] = None,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.n_exec += 1
        y = _finite(throughput)
        if success and y > self.best_perf:
            self.best_perf = y
        if (
            success
            and self.best_perf > 0
            and y >= 0.95 * self.best_perf
            and self.t_opt is None
        ):
            # Tentative; finalize in summary after best is known.
            self.evals_to_opt95 = self.n_exec
            self.t_opt = time.time() - self.started_at
        row = {
            "n_exec": self.n_exec,
            "throughput": y,
            "success": bool(success),
            "wall_offset": time.time() - self.started_at,
        }
        if prediction:
            row["prediction"] = dict(prediction)
            if success and y > 0:
                self.gpr_pairs.append(
                    {
                        "y_true": y,
                        "y_pred": _finite(prediction.get("mean")),
                        "std": _finite(prediction.get("std"), 1.0),
                        "confidence": _finite(prediction.get("confidence"), 0.5),
                        "failure_probability": _finite(
                            prediction.get("failure_probability"), 0.5
                        ),
                    }
                )
        if meta:
            row["meta"] = dict(meta)
        self.history.append(row)

    def record_llm(self, calls: int = 1, tokens: int = 0) -> None:
        self.llm_calls += max(0, int(calls))
        self.tokens += max(0, int(tokens))

    def record_action(self, action: str) -> None:
        self.actions.append(str(action))

    @property
    def r_filter(self) -> float:
        return (self.n_filtered / self.n_target) if self.n_target else 0.0

    def finalize_t_opt(self) -> None:
        """Recompute T_opt against final best_perf."""
        if self.best_perf <= 0:
            self.t_opt = None
            self.evals_to_opt95 = None
            return
        threshold = 0.95 * self.best_perf
        self.t_opt = None
        self.evals_to_opt95 = None
        for row in self.history:
            if row.get("success") and _finite(row.get("throughput")) >= threshold:
                self.evals_to_opt95 = int(row["n_exec"])
                self.t_opt = float(row["wall_offset"])
                return

    def pie(self) -> float:
        self.finalize_t_opt()
        gain = max(0.0, self.best_perf - self.y0)
        denom = self.t_opt if self.t_opt and self.t_opt > 0 else max(1.0, time.time() - self.started_at)
        return gain / denom

    def pie_per_exec(self) -> float:
        if self.y0 <= 0:
            rel = 0.0 if self.best_perf <= 0 else 1.0
        else:
            rel = max(0.0, self.best_perf / self.y0 - 1.0)
        return rel / max(1, self.n_exec)

    def summary(self, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        self.finalize_t_opt()
        payload = {
            "best_perf": self.best_perf,
            "y0": self.y0,
            "T_opt": self.t_opt,
            "evals_to_opt95": self.evals_to_opt95,
            "PIE": self.pie(),
            "PIE_per_exec": self.pie_per_exec(),
            "N_exec": self.n_exec,
            "N_target": self.n_target,
            "N_filtered": self.n_filtered,
            "R_filter": self.r_filter,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "actions": list(self.actions),
            "wall_time_seconds": time.time() - self.started_at,
        }
        if extra:
            payload.update(dict(extra))
        return payload

    def write_json(self, path: Path | str, extra: Optional[Mapping[str, Any]] = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary(extra), indent=2) + "\n", encoding="utf-8")


def compute_gpr_regression_metrics(
    pairs: Sequence[Mapping[str, Any]],
    conf_bins: Sequence[tuple[float, float]] = (
        (0.0, 0.33),
        (0.33, 0.67),
        (0.67, 1.01),
    ),
) -> Dict[str, Any]:
    """MAE / RMSE / R^2 overall and by confidence strata."""

    def _stats(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"n": 0, "MAE": None, "RMSE": None, "R2": None}
        y_true = [_finite(r.get("y_true")) for r in rows]
        y_pred = [_finite(r.get("y_pred")) for r in rows]
        n = len(y_true)
        abs_err = [abs(a - b) for a, b in zip(y_true, y_pred)]
        sq_err = [(a - b) ** 2 for a, b in zip(y_true, y_pred)]
        mae = sum(abs_err) / n
        rmse = math.sqrt(sum(sq_err) / n)
        mean_y = sum(y_true) / n
        ss_tot = sum((y - mean_y) ** 2 for y in y_true)
        ss_res = sum(sq_err)
        r2 = None if ss_tot <= 1e-12 else 1.0 - ss_res / ss_tot
        return {"n": n, "MAE": mae, "RMSE": rmse, "R2": r2}

    overall = _stats(list(pairs))
    strata = []
    for low, high in conf_bins:
        bucket = [
            row
            for row in pairs
            if low <= _finite(row.get("confidence"), 0.5) < high
        ]
        strata.append({"conf_low": low, "conf_high": high, **_stats(bucket)})
    return {"overall": overall, "by_confidence": strata}
