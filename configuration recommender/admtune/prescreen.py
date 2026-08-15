"""3D risk + performance + confidence pre-screen and R_filter accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


def confidence_from_prediction(prediction: Mapping[str, Any]) -> float:
    mean = float(prediction.get("mean") or 0.0)
    std = float(prediction.get("std") or 0.0)
    return max(0.0, min(1.0, 1.0 / (1.0 + std / max(1e-9, abs(mean) + std))))


@dataclass
class PrescreenResult:
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    n_target: int = 0
    n_accepted: int = 0
    n_filtered: int = 0
    R_filter: float = 0.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_target": self.n_target,
            "n_accepted": self.n_accepted,
            "n_filtered": self.n_filtered,
            "R_filter": self.R_filter,
            "diagnostics": dict(self.diagnostics),
            "accepted": self.accepted,
            "rejected": self.rejected,
        }


def three_d_prescreen(
    ranked: Sequence[Mapping[str, Any]],
    *,
    top_k: int = 2,
    risk_threshold: float = 0.75,
    conf_threshold: float = 0.2,
    enabled: bool = True,
) -> PrescreenResult:
    """Filter surrogate-ranked candidates with risk/perf/confidence gates.

    ``ranked`` items should look like SurrogateModel.rank_candidates outputs:
    ``{config, prediction, acquisition, feasible, ...}``.
    When ``enabled`` is False, the first ``top_k`` are accepted (ablation).
    """
    items = list(ranked)
    n_target = len(items)
    if not enabled:
        accepted = [dict(item) for item in items[: max(0, int(top_k))]]
        rejected = [dict(item) for item in items[max(0, int(top_k)) :]]
        for item in accepted:
            pred = dict(item.get("prediction") or {})
            pred["confidence"] = confidence_from_prediction(pred)
            item["prediction"] = pred
            item["prescreen"] = "bypass"
        return PrescreenResult(
            accepted=accepted,
            rejected=rejected,
            n_target=n_target,
            n_accepted=len(accepted),
            n_filtered=len(rejected),
            R_filter=(len(rejected) / n_target) if n_target else 0.0,
            diagnostics={"enabled": False, "fallback": "top_k_only"},
        )

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in items:
        row = dict(item)
        pred = dict(row.get("prediction") or {})
        conf = confidence_from_prediction(pred)
        pred["confidence"] = conf
        row["prediction"] = pred
        p_fail = float(pred.get("failure_probability") or 0.0)
        feasible = bool(row.get("feasible", True))
        ok = (
            feasible
            and p_fail < float(risk_threshold)
            and conf >= float(conf_threshold)
        )
        if ok and len(accepted) < max(0, int(top_k)):
            row["prescreen"] = "accept"
            accepted.append(row)
        else:
            row["prescreen"] = "reject"
            row["reject_reason"] = {
                "p_fail": p_fail,
                "confidence": conf,
                "feasible": feasible,
                "top_k_full": len(accepted) >= max(0, int(top_k)),
            }
            rejected.append(row)

    # Ensure at least one candidate when possible (avoid empty Execute).
    if not accepted and items:
        best = dict(items[0])
        pred = dict(best.get("prediction") or {})
        pred["confidence"] = confidence_from_prediction(pred)
        best["prediction"] = pred
        best["prescreen"] = "accept_fallback_best"
        accepted = [best]
        rejected = [dict(x) for x in items[1:]]

    n_accepted = len(accepted)
    n_filtered = max(0, n_target - n_accepted)
    return PrescreenResult(
        accepted=accepted,
        rejected=rejected,
        n_target=n_target,
        n_accepted=n_accepted,
        n_filtered=n_filtered,
        R_filter=(n_filtered / n_target) if n_target else 0.0,
        diagnostics={
            "enabled": True,
            "risk_threshold": risk_threshold,
            "conf_threshold": conf_threshold,
            "top_k": top_k,
        },
    )
