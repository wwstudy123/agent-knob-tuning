"""Trigger_t and Delta_M_t reflection policy with three modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass
class TriggerContext:
    action: str = "Execute"
    improve_delta: float = 0.0
    failed: bool = False
    stagnation_len: int = 0
    phase_changed: bool = False
    improve_threshold: float = 0.03
    stagnation_limit: int = 4


def build_delta_m(
    *,
    knobs_now: Mapping[str, Any],
    knobs_prev: Optional[Mapping[str, Any]] = None,
    metrics_now: Optional[Mapping[str, Any]] = None,
    metrics_prev: Optional[Mapping[str, Any]] = None,
    score_now: float = 0.0,
    score_prev_best: float = 0.0,
    action: str = "Execute",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    prev_k = dict(knobs_prev or {})
    now_k = dict(knobs_now or {})
    delta_knobs = {
        key: {"from": prev_k.get(key), "to": now_k.get(key)}
        for key in sorted(set(prev_k) | set(now_k))
        if prev_k.get(key) != now_k.get(key)
    }
    prev_m = dict(metrics_prev or {})
    now_m = dict(metrics_now or {})
    delta_metrics = {}
    for key in sorted(set(prev_m) | set(now_m)):
        try:
            before = float(prev_m.get(key)) if prev_m.get(key) is not None else None
            after = float(now_m.get(key)) if now_m.get(key) is not None else None
        except (TypeError, ValueError):
            continue
        if before != after:
            delta_metrics[key] = {"from": before, "to": after}
    payload = {
        "delta_knobs": delta_knobs,
        "delta_metrics": delta_metrics,
        "score_prev_best": score_prev_best,
        "score_now": score_now,
        "action": action,
    }
    if notes:
        payload["notes"] = str(notes)
    return payload


class ReflectionPolicy:
    """full_history | unconstrained_delta | conditional_delta."""

    MODES = ("full_history", "unconstrained_delta", "conditional_delta", "off")

    def __init__(self, mode: str = "conditional_delta") -> None:
        mode = str(mode or "conditional_delta")
        if mode not in self.MODES:
            mode = "conditional_delta"
        self.mode = mode

    def trigger(self, ctx: TriggerContext) -> bool:
        if self.mode == "off":
            return False
        if self.mode == "unconstrained_delta":
            return True
        if self.mode == "full_history":
            return True
        # conditional_delta
        if ctx.action in {"Reselect", "Reanalyze", "AdjustRange"}:
            return True
        if ctx.failed:
            return True
        if ctx.phase_changed:
            return True
        if abs(ctx.improve_delta) >= ctx.improve_threshold:
            return True
        if ctx.stagnation_len >= ctx.stagnation_limit:
            return True
        return False

    def prompt_history(
        self,
        *,
        triggered: bool,
        full_history_text: str,
        delta_m: Mapping[str, Any],
        compact_reflection: str,
    ) -> Dict[str, Any]:
        """Choose what to inject into the LLM prompt."""
        if self.mode == "off" or not triggered:
            return {
                "triggered": False,
                "mode": self.mode,
                "history_text": compact_reflection,
                "delta_m": None,
                "skip_llm": self.mode == "conditional_delta" and not triggered,
            }
        if self.mode == "full_history":
            return {
                "triggered": True,
                "mode": self.mode,
                "history_text": full_history_text or compact_reflection,
                "delta_m": None,
                "skip_llm": False,
            }
        # delta modes
        import json

        delta_text = json.dumps(dict(delta_m), indent=2, ensure_ascii=False)
        return {
            "triggered": True,
            "mode": self.mode,
            "history_text": (
                compact_reflection + "\n\nDelta_M_t:\n" + delta_text
                if compact_reflection
                else "Delta_M_t:\n" + delta_text
            ),
            "delta_m": dict(delta_m),
            "skip_llm": False,
        }
