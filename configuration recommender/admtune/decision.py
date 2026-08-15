"""Map AgentTune controller outputs to ADMTune Execute/Reselect/Reanalyze/AdjustRange."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

ADMTUNE_ACTIONS = ("Execute", "Reselect", "Reanalyze", "AdjustRange", "Stop")

_LEGACY_TO_ADM = {
    "continue_local": "Execute",
    "increase_exploration": "Execute",
    "rollback_safe": "Execute",
    "reflect": "Execute",
    "reselect_knobs": "Reselect",
    "expand_ranges": "AdjustRange",
    "shrink_ranges": "AdjustRange",
    "stop": "Stop",
}


def map_legacy_action(name: str) -> str:
    return _LEGACY_TO_ADM.get(str(name), "Execute")


def decide_admtune(
    state: Optional[Mapping[str, Any]] = None,
    *,
    enabled: bool = True,
    drift_score: float = 0.0,
    stagnation_len: int = 0,
    force_action: Optional[str] = None,
    legacy_action: Optional[str] = None,
) -> Tuple[str, str]:
    """Return (admtune_action, reason).

    When ``enabled`` is False the pipeline is fixed to Execute (ablation: no_decision).
    """
    if force_action:
        action = force_action if force_action in ADMTUNE_ACTIONS else "Execute"
        return action, "forced"
    if not enabled:
        return "Execute", "decision_disabled_fixed_execute"

    if legacy_action == "stop" or (state or {}).get("budget_exhausted"):
        return "Stop", "budget_or_legacy_stop"

    fail_rate = float((state or {}).get("failure_rate") or 0.0)
    improve = (state or {}).get("improvement_rate")
    conf = (state or {}).get("surrogate_confidence")

    if drift_score >= 0.5:
        return "Reanalyze", "workload_or_metric_drift"
    if fail_rate >= 0.35:
        return "AdjustRange", "elevated_failure_rate"
    if stagnation_len >= 4 and (conf is None or float(conf) >= 0.5):
        return "Reselect", "stagnation_with_confidence"
    if (
        improve is not None
        and float(improve) >= 0.05
        and conf is not None
        and float(conf) >= 0.6
    ):
        return "AdjustRange", "strong_improvement_widen_or_shrink"
    if legacy_action:
        return map_legacy_action(legacy_action), f"legacy:{legacy_action}"
    return "Execute", "default_execute"


def action_prompt_instruction(action: str, reason: str = "") -> str:
    guides = {
        "Execute": "Keep the current knob set and ranges; propose local refinements around the best config.",
        "Reselect": "Propose a different subset of important knobs; de-emphasize ineffective ones.",
        "Reanalyze": "Reinterpret workload pressure from recent inner metrics; adapt recommendations to the new bottleneck.",
        "AdjustRange": "Tighten unsafe ranges or expand promising numeric ranges before proposing values.",
        "Stop": "Do not propose new configurations; search should stop.",
    }
    body = guides.get(action, guides["Execute"])
    return f"ADMTune action={action}. Reason={reason or 'n/a'}. Guidance: {body}"
