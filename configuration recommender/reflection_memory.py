"""Bounded, deterministic reflection memory for iterative configuration tuning.

The module deliberately has no dependency on an LLM or on the rest of the
configuration recommender.  Its serialized form is suitable for checkpoints.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional


STATE_VERSION = 1


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def static_context_digest(context: Any) -> str:
    """Return a stable SHA-256 digest for arbitrary JSON-like context."""
    if isinstance(context, str):
        payload = context
    else:
        payload = _canonical(context)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compact_trial(trial: Mapping[str, Any]) -> Dict[str, Any]:
    result = {
        "turn": trial.get("turn"),
        "trial_id": trial.get("trial_id"),
        "score": trial.get("score"),
        "success": bool(trial.get("success", True)),
        "knobs": deepcopy(dict(trial.get("knobs") or {})),
        "metrics": deepcopy(dict(trial.get("metrics") or {})),
    }
    if trial.get("notes"):
        result["notes"] = str(trial["notes"])
    return {key: value for key, value in result.items() if value is not None}


class ReflectionMemory:
    """Incremental reflection state with bounded histories.

    Scores are maximized unless ``objective="minimize"`` is supplied.
    ``update_from_trial`` accepts either a trial mapping or explicit keyword
    arguments, making the class easy to use from existing tuning loops.
    """

    def __init__(
        self,
        static_context: Any = "",
        *,
        static_context_hash: Optional[str] = None,
        version: int = STATE_VERSION,
        token_budget: int = 4096,
        summary_interval: int = 5,
        summary_char_budget: int = 4000,
        recent_delta_limit: int = 8,
        lesson_limit: int = 12,
        pinned_limit: int = 3,
        objective: str = "maximize",
    ) -> None:
        if objective not in ("maximize", "minimize"):
            raise ValueError("objective must be 'maximize' or 'minimize'")
        for name, value in (
            ("token_budget", token_budget),
            ("summary_interval", summary_interval),
            ("summary_char_budget", summary_char_budget),
            ("recent_delta_limit", recent_delta_limit),
            ("lesson_limit", lesson_limit),
            ("pinned_limit", pinned_limit),
        ):
            if int(value) < 1:
                raise ValueError("%s must be positive" % name)

        self.version = int(version)
        self.static_context_hash = (
            static_context_hash or static_context_digest(static_context)
        )
        self.turn = 0
        self.rolling_summary = ""
        self.pinned_trials: Dict[str, List[Dict[str, Any]]] = {
            "best": [],
            "failure": [],
            "diverse": [],
        }
        self.recent_deltas: List[Dict[str, Any]] = []
        self.lessons: List[str] = []
        self.token_budget = int(token_budget)
        self.token_usage: Dict[str, int] = {
            "prompt": 0,
            "completion": 0,
            "total": 0,
        }
        self.summary_interval = int(summary_interval)
        self.summary_char_budget = int(summary_char_budget)
        self.recent_delta_limit = int(recent_delta_limit)
        self.lesson_limit = int(lesson_limit)
        self.pinned_limit = int(pinned_limit)
        self.objective = objective
        self._turns_since_summary = 0
        self._chars_since_summary = 0

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_budget - self.token_usage["total"])

    def record_token_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        *,
        total_tokens: Optional[int] = None,
    ) -> Dict[str, int]:
        """Accumulate non-negative token counts and return current accounting."""
        prompt_tokens = int(prompt_tokens)
        completion_tokens = int(completion_tokens)
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        total_tokens = int(total_tokens)
        if total_tokens < 0:
            raise ValueError("total_tokens cannot be negative")
        self.token_usage["prompt"] += prompt_tokens
        self.token_usage["completion"] += completion_tokens
        self.token_usage["total"] += total_tokens
        return dict(self.token_usage)

    def should_summarize(self, additional_chars: int = 0) -> bool:
        """Whether the interval or accumulated-character threshold is due."""
        return (
            self._turns_since_summary >= self.summary_interval
            or self._chars_since_summary + max(0, int(additional_chars))
            >= self.summary_char_budget
        )

    # A readable alias for callers that use "summary due" terminology.
    is_summary_due = should_summarize

    def update_from_trial(
        self,
        trial: Optional[Mapping[str, Any]] = None,
        *,
        knobs: Optional[Mapping[str, Any]] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        score: Optional[float] = None,
        success: bool = True,
        trial_id: Any = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Incorporate one trial and return its compact delta record."""
        supplied = dict(trial or {})
        knobs = dict(knobs if knobs is not None else supplied.get("knobs") or {})
        metrics = dict(
            metrics if metrics is not None else supplied.get("metrics") or {}
        )
        score = score if score is not None else supplied.get("score")
        success = bool(supplied.get("success", success))
        trial_id = trial_id if trial_id is not None else supplied.get("trial_id")
        notes = notes if notes is not None else supplied.get("notes")

        previous_best = self._best_trial()
        self.turn += 1
        current = _compact_trial(
            {
                "turn": self.turn,
                "trial_id": trial_id,
                "score": score,
                "success": success,
                "knobs": knobs,
                "metrics": metrics,
                "notes": notes,
            }
        )
        delta = self._make_delta(current, previous_best)
        self._append_bounded(self.recent_deltas, delta, self.recent_delta_limit)
        self._pin_trial(current, delta)
        lesson = self._lesson_for(current, delta, previous_best)
        if lesson:
            self._append_unique_bounded(self.lessons, lesson, self.lesson_limit)

        encoded_chars = len(_canonical(delta))
        self._turns_since_summary += 1
        self._chars_since_summary += encoded_chars
        if self.should_summarize():
            self.refresh_summary()
        return deepcopy(delta)

    add_trial = update_from_trial

    def refresh_summary(self) -> str:
        """Regenerate a deterministic rolling summary from bounded state."""
        best = self._best_trial()
        failures = self.pinned_trials["failure"]
        changed = []
        for delta in self.recent_deltas[-3:]:
            names = list(delta.get("knob_delta", {}))
            changed.append(
                "t%s:%s" % (delta.get("turn", "?"), ",".join(names) or "no-change")
            )
        parts = ["turn=%d" % self.turn]
        if best:
            parts.append(
                "best=t%s score=%s"
                % (best.get("turn", "?"), self._format_value(best.get("score")))
            )
        else:
            parts.append("best=none")
        parts.append("failures=%d" % len(failures))
        if changed:
            parts.append("recent=" + ";".join(changed))
        if self.lessons:
            parts.append("lessons=" + " | ".join(self.lessons[-3:]))
        self.rolling_summary = ". ".join(parts)
        self._turns_since_summary = 0
        self._chars_since_summary = 0
        return self.rolling_summary

    def compact_prompt_context(self, max_chars: Optional[int] = None) -> str:
        """Return compact deterministic JSON suitable for an LLM prompt."""
        context = {
            "v": self.version,
            "static": self.static_context_hash[:16],
            "turn": self.turn,
            "summary": self.rolling_summary,
            "best": self._best_trial(),
            "failures": self.pinned_trials["failure"][-1:],
            "diverse": self.pinned_trials["diverse"][-1:],
            "recent": self.recent_deltas[-3:],
            "lessons": self.lessons[-3:],
            "tokens": {
                "used": self.token_usage["total"],
                "budget": self.token_budget,
                "remaining": self.tokens_remaining,
            },
        }
        text = _canonical(context)
        if max_chars is None:
            max_chars = self.summary_char_budget
        max_chars = int(max_chars)
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        if len(text) <= max_chars:
            return text

        # Preserve valid JSON when aggressively compacting.
        minimal = {
            "v": self.version,
            "static": self.static_context_hash[:16],
            "turn": self.turn,
            "summary": self.rolling_summary,
            "tokens": context["tokens"],
        }
        text = _canonical(minimal)
        if len(text) <= max_chars:
            return text
        minimal["summary"] = ""
        text = _canonical(minimal)
        if len(text) <= max_chars:
            return text
        tiny = _canonical({"turn": self.turn})
        if len(tiny) <= max_chars:
            return tiny
        return "{}" if max_chars >= 2 else ""

    build_prompt_context = compact_prompt_context

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep, JSON-serializable state snapshot."""
        return {
            "version": self.version,
            "static_context_hash": self.static_context_hash,
            "turn": self.turn,
            "rolling_summary": self.rolling_summary,
            "pinned_trials": deepcopy(self.pinned_trials),
            "recent_deltas": deepcopy(self.recent_deltas),
            "lessons": list(self.lessons),
            "token_budget": self.token_budget,
            "token_usage": dict(self.token_usage),
            "summary_interval": self.summary_interval,
            "summary_char_budget": self.summary_char_budget,
            "recent_delta_limit": self.recent_delta_limit,
            "lesson_limit": self.lesson_limit,
            "pinned_limit": self.pinned_limit,
            "objective": self.objective,
            "turns_since_summary": self._turns_since_summary,
            "chars_since_summary": self._chars_since_summary,
        }

    @classmethod
    def from_dict(cls, state: Mapping[str, Any]) -> "ReflectionMemory":
        """Restore state produced by :meth:`to_dict`."""
        memory = cls(
            static_context_hash=str(state["static_context_hash"]),
            version=int(state.get("version", STATE_VERSION)),
            token_budget=int(state.get("token_budget", 4096)),
            summary_interval=int(state.get("summary_interval", 5)),
            summary_char_budget=int(state.get("summary_char_budget", 4000)),
            recent_delta_limit=int(state.get("recent_delta_limit", 8)),
            lesson_limit=int(state.get("lesson_limit", 12)),
            pinned_limit=int(state.get("pinned_limit", 3)),
            objective=str(state.get("objective", "maximize")),
        )
        memory.turn = int(state.get("turn", 0))
        memory.rolling_summary = str(state.get("rolling_summary", ""))
        pins = state.get("pinned_trials") or {}
        memory.pinned_trials = {
            name: deepcopy(list(pins.get(name) or []))[-memory.pinned_limit :]
            for name in ("best", "failure", "diverse")
        }
        memory.recent_deltas = deepcopy(list(state.get("recent_deltas") or []))[
            -memory.recent_delta_limit :
        ]
        memory.lessons = [
            str(item) for item in list(state.get("lessons") or [])[-memory.lesson_limit :]
        ]
        usage = state.get("token_usage") or {}
        memory.token_usage = {
            "prompt": max(0, int(usage.get("prompt", 0))),
            "completion": max(0, int(usage.get("completion", 0))),
            "total": max(0, int(usage.get("total", 0))),
        }
        memory._turns_since_summary = max(
            0, int(state.get("turns_since_summary", 0))
        )
        memory._chars_since_summary = max(
            0, int(state.get("chars_since_summary", 0))
        )
        return memory

    def _best_trial(self) -> Optional[Dict[str, Any]]:
        if not self.pinned_trials["best"]:
            return None
        return self.pinned_trials["best"][0]

    def _is_better(self, left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_score = _number(left.get("score"))
        right_score = _number(right.get("score"))
        if left_score is None:
            return False
        if right_score is None:
            return True
        if self.objective == "maximize":
            return left_score > right_score
        return left_score < right_score

    def _pin_trial(
        self, trial: Dict[str, Any], delta: Mapping[str, Any]
    ) -> None:
        if trial.get("success", True) and _number(trial.get("score")) is not None:
            best = self.pinned_trials["best"]
            best.append(deepcopy(trial))
            reverse = self.objective == "maximize"
            best.sort(key=lambda item: float(item["score"]), reverse=reverse)
            del best[self.pinned_limit :]
        if not trial.get("success", True):
            self._append_bounded(
                self.pinned_trials["failure"], deepcopy(trial), self.pinned_limit
            )

        diverse = deepcopy(trial)
        diverse["novelty"] = len(delta.get("knob_delta", {}))
        candidates = self.pinned_trials["diverse"]
        candidates.append(diverse)
        candidates.sort(
            key=lambda item: (-int(item.get("novelty", 0)), int(item.get("turn", 0)))
        )
        del candidates[self.pinned_limit :]

    def _make_delta(
        self,
        current: Mapping[str, Any],
        best: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        baseline_knobs = dict((best or {}).get("knobs") or {})
        current_knobs = dict(current.get("knobs") or {})
        knob_delta: Dict[str, Dict[str, Any]] = {}
        for name in sorted(set(baseline_knobs) | set(current_knobs)):
            before = baseline_knobs.get(name)
            after = current_knobs.get(name)
            if before != after:
                change: Dict[str, Any] = {"from": before, "to": after}
                before_num, after_num = _number(before), _number(after)
                if before_num is not None and after_num is not None:
                    change["change"] = after_num - before_num
                knob_delta[name] = change

        highlights = self._metric_highlights(
            dict((best or {}).get("metrics") or {}),
            dict(current.get("metrics") or {}),
        )
        result: Dict[str, Any] = {
            "turn": current.get("turn"),
            "trial_id": current.get("trial_id"),
            "vs_best_turn": (best or {}).get("turn"),
            "knob_delta": knob_delta,
            "metric_highlights": highlights,
            "score": current.get("score"),
            "success": current.get("success", True),
        }
        return {key: value for key, value in result.items() if value is not None}

    @staticmethod
    def _metric_highlights(
        baseline: Mapping[str, Any], current: Mapping[str, Any], limit: int = 5
    ) -> Dict[str, Dict[str, Any]]:
        rows = []
        for name in sorted(current):
            value = current[name]
            item: Dict[str, Any] = {"value": value}
            old_num, new_num = _number(baseline.get(name)), _number(value)
            magnitude = 0.0
            if old_num is not None and new_num is not None:
                item["change"] = new_num - old_num
                if old_num:
                    item["percent_change"] = (new_num - old_num) / abs(old_num) * 100.0
                magnitude = abs(new_num - old_num)
            rows.append((magnitude, name, item))
        rows.sort(key=lambda row: (-row[0], row[1]))
        return {name: item for _, name, item in rows[:limit]}

    def _lesson_for(
        self,
        current: Mapping[str, Any],
        delta: Mapping[str, Any],
        previous_best: Optional[Mapping[str, Any]],
    ) -> str:
        names = sorted(delta.get("knob_delta", {}))
        knobs = ",".join(names[:4]) or "no knobs"
        if not current.get("success", True):
            return "Avoid failed combination at t%d (%s)." % (self.turn, knobs)
        if previous_best is None and _number(current.get("score")) is not None:
            return "Established baseline score %s." % self._format_value(
                current.get("score")
            )
        if previous_best and self._is_better(current, previous_best):
            return "Changing %s improved score to %s." % (
                knobs,
                self._format_value(current.get("score")),
            )
        if _number(current.get("score")) is not None:
            return "Changing %s did not beat the best score." % knobs
        return ""

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return format(value, ".6g")
        return str(value)

    @staticmethod
    def _append_bounded(target: List[Any], value: Any, limit: int) -> None:
        target.append(value)
        del target[:-limit]

    @classmethod
    def _append_unique_bounded(
        cls, target: List[str], value: str, limit: int
    ) -> None:
        if value in target:
            target.remove(value)
        cls._append_bounded(target, value, limit)


__all__ = ["ReflectionMemory", "STATE_VERSION", "static_context_digest"]
