"""Deterministic, rule-based control for database tuning experiments.

The module deliberately has no dependencies on the rest of the recommender.
Callers record completed trials and ask the controller for the next high-level
action.  Every decision includes the rule that fired and a state snapshot.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


ACTIONS = (
    "continue_local",
    "expand_ranges",
    "shrink_ranges",
    "reselect_knobs",
    "increase_exploration",
    "rollback_safe",
    "reflect",
    "stop",
)


def _optional_number(value: Any, name: str) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % name)
    return number


@dataclass
class TrialResult:
    """One completed tuning trial.

    ``throughput`` may be omitted for failed trials.  ``configuration`` is used
    only to estimate search diversity, so its values need only be JSON-safe.
    """

    throughput: Optional[float] = None
    success: bool = True
    configuration: Dict[str, Any] = field(default_factory=dict)
    surrogate_confidence: Optional[float] = None
    benchmark_cost: int = 1
    token_cost: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.throughput = _optional_number(self.throughput, "throughput")
        self.surrogate_confidence = _optional_number(
            self.surrogate_confidence, "surrogate_confidence"
        )
        if self.surrogate_confidence is not None and not (
            0.0 <= self.surrogate_confidence <= 1.0
        ):
            raise ValueError("surrogate_confidence must be between 0 and 1")
        if self.benchmark_cost < 0 or self.token_cost < 0:
            raise ValueError("trial costs cannot be negative")
        if self.success and self.throughput is None:
            raise ValueError("a successful trial requires throughput")
        self.configuration = copy.deepcopy(dict(self.configuration))
        self.metadata = copy.deepcopy(dict(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrialResult":
        return cls(**dict(data))


@dataclass(frozen=True)
class ControllerState:
    """Derived state for the current recent-trial window."""

    trial_count: int
    successful_trials: int
    best_throughput: Optional[float]
    improvement_rate: Optional[float]
    failure_rate: float
    diversity: Optional[float]
    surrogate_confidence: Optional[float]
    benchmark_used: int
    benchmark_budget: Optional[int]
    benchmark_remaining: Optional[int]
    tokens_used: int
    token_budget: Optional[int]
    tokens_remaining: Optional[int]
    budget_exhausted: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ControllerState":
        return cls(**dict(data))


@dataclass(frozen=True)
class ControllerAction:
    """An explainable policy decision."""

    name: str
    reason: str
    rule: str
    state: ControllerState

    def __post_init__(self) -> None:
        if self.name not in ACTIONS:
            raise ValueError("unknown controller action: %s" % self.name)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ControllerAction":
        values = dict(data)
        values["state"] = ControllerState.from_dict(values["state"])
        return cls(**values)


class DecisionController:
    """Choose the next tuning action from a bounded window of trial results.

    Rules are evaluated in safety-first order.  All thresholds are constructor
    arguments and are persisted by :meth:`to_dict`.
    """

    _SERIAL_VERSION = 1
    _SETTING_NAMES = (
        "window_size",
        "min_trials",
        "reselect_after",
        "reflect_after",
        "failure_rate_rollback",
        "failure_rate_shrink",
        "improvement_expand",
        "improvement_stagnant",
        "diversity_low",
        "confidence_high",
        "confidence_low",
        "benchmark_budget",
        "token_budget",
    )

    def __init__(
        self,
        window_size: int = 8,
        min_trials: int = 3,
        reselect_after: int = 6,
        reflect_after: int = 4,
        failure_rate_rollback: float = 0.50,
        failure_rate_shrink: float = 0.25,
        improvement_expand: float = 0.05,
        improvement_stagnant: float = 0.01,
        diversity_low: float = 0.50,
        confidence_high: float = 0.70,
        confidence_low: float = 0.40,
        benchmark_budget: Optional[int] = None,
        token_budget: Optional[int] = None,
    ) -> None:
        self.window_size = int(window_size)
        self.min_trials = int(min_trials)
        self.reselect_after = int(reselect_after)
        self.reflect_after = int(reflect_after)
        self.failure_rate_rollback = float(failure_rate_rollback)
        self.failure_rate_shrink = float(failure_rate_shrink)
        self.improvement_expand = float(improvement_expand)
        self.improvement_stagnant = float(improvement_stagnant)
        self.diversity_low = float(diversity_low)
        self.confidence_high = float(confidence_high)
        self.confidence_low = float(confidence_low)
        self.benchmark_budget = (
            None if benchmark_budget is None else int(benchmark_budget)
        )
        self.token_budget = None if token_budget is None else int(token_budget)
        self._validate_settings()
        self.trial_history: List[TrialResult] = []
        self.state_history: List[ControllerState] = []
        self.action_history: List[ControllerAction] = []

    def _validate_settings(self) -> None:
        if min(
            self.window_size, self.min_trials, self.reselect_after, self.reflect_after
        ) <= 0:
            raise ValueError("window and trial-count thresholds must be positive")
        for name in (
            "failure_rate_rollback",
            "failure_rate_shrink",
            "diversity_low",
            "confidence_high",
            "confidence_low",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError("%s must be between 0 and 1" % name)
        if self.failure_rate_shrink >= self.failure_rate_rollback:
            raise ValueError("failure_rate_shrink must be below rollback threshold")
        if self.confidence_low > self.confidence_high:
            raise ValueError("confidence_low cannot exceed confidence_high")
        if self.improvement_stagnant < 0 or self.improvement_expand <= 0:
            raise ValueError("improvement thresholds must be non-negative")
        if self.improvement_stagnant >= self.improvement_expand:
            raise ValueError("stagnant threshold must be below expand threshold")
        if self.benchmark_budget is not None and self.benchmark_budget < 0:
            raise ValueError("benchmark_budget cannot be negative")
        if self.token_budget is not None and self.token_budget < 0:
            raise ValueError("token_budget cannot be negative")

    def record_trial(
        self, trial: Union[TrialResult, Mapping[str, Any]]
    ) -> ControllerState:
        """Append one result and return the newly derived state."""

        if isinstance(trial, Mapping):
            result = TrialResult.from_dict(trial)
        elif isinstance(trial, TrialResult):
            result = TrialResult.from_dict(trial.to_dict())
        else:
            raise TypeError("trial must be a TrialResult or mapping")
        self.trial_history.append(result)
        return self.state

    @property
    def state(self) -> ControllerState:
        """Compute state deterministically from history."""

        recent = self.trial_history[-self.window_size :]
        successful = [
            trial
            for trial in recent
            if trial.success and trial.throughput is not None
        ]
        throughputs = [trial.throughput for trial in successful]
        best = max(throughputs) if throughputs else None

        improvement = None
        if len(throughputs) >= 2:
            first, last = throughputs[0], throughputs[-1]
            denominator = abs(first)
            if denominator > 1e-12:
                improvement = (last - first) / denominator
            elif abs(last) <= 1e-12:
                improvement = 0.0

        diversity = None
        configured = [trial for trial in successful if trial.configuration]
        if configured:
            fingerprints = {
                json.dumps(
                    trial.configuration,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=repr,
                )
                for trial in configured
            }
            diversity = len(fingerprints) / float(len(configured))

        confidences = [
            trial.surrogate_confidence
            for trial in successful
            if trial.surrogate_confidence is not None
        ]
        confidence = (
            sum(confidences) / float(len(confidences)) if confidences else None
        )
        benchmark_used = sum(trial.benchmark_cost for trial in self.trial_history)
        tokens_used = sum(trial.token_cost for trial in self.trial_history)
        benchmark_remaining = self._remaining(
            self.benchmark_budget, benchmark_used
        )
        tokens_remaining = self._remaining(self.token_budget, tokens_used)
        exhausted = (
            benchmark_remaining == 0
            if benchmark_remaining is not None
            else False
        ) or (tokens_remaining == 0 if tokens_remaining is not None else False)

        return ControllerState(
            trial_count=len(recent),
            successful_trials=len(successful),
            best_throughput=best,
            improvement_rate=improvement,
            failure_rate=(
                sum(not trial.success for trial in recent) / float(len(recent))
                if recent
                else 0.0
            ),
            diversity=diversity,
            surrogate_confidence=confidence,
            benchmark_used=benchmark_used,
            benchmark_budget=self.benchmark_budget,
            benchmark_remaining=benchmark_remaining,
            tokens_used=tokens_used,
            token_budget=self.token_budget,
            tokens_remaining=tokens_remaining,
            budget_exhausted=exhausted,
        )

    @staticmethod
    def _remaining(limit: Optional[int], used: int) -> Optional[int]:
        return None if limit is None else max(0, limit - used)

    def decide(self) -> ControllerAction:
        """Apply the policy, append the decision, and return it."""

        state = self.state
        name, rule, reason = self._apply_policy(state)
        action = ControllerAction(name=name, reason=reason, rule=rule, state=state)
        self.state_history.append(state)
        self.action_history.append(action)
        return action

    def _apply_policy(self, state: ControllerState) -> Sequence[str]:
        if state.budget_exhausted:
            return (
                "stop",
                "budget_exhausted",
                "Benchmark or token budget is exhausted.",
            )
        if state.trial_count == 0:
            return (
                "continue_local",
                "no_history",
                "No evidence is available; use the existing safe local search.",
            )
        if (
            state.trial_count >= self.min_trials
            and state.failure_rate >= self.failure_rate_rollback
        ):
            return (
                "rollback_safe",
                "high_failure_rate",
                "Recent failure rate %.3f meets rollback threshold %.3f."
                % (state.failure_rate, self.failure_rate_rollback),
            )
        if state.successful_trials < self.min_trials:
            return (
                "continue_local",
                "sparse_history",
                "Only %d successful trial(s); at least %d are required."
                % (state.successful_trials, self.min_trials),
            )
        if state.failure_rate >= self.failure_rate_shrink:
            return (
                "shrink_ranges",
                "elevated_failure_rate",
                "Recent failure rate %.3f meets shrink threshold %.3f."
                % (state.failure_rate, self.failure_rate_shrink),
            )

        confidence = state.surrogate_confidence
        improvement = state.improvement_rate
        if (
            improvement is not None
            and improvement >= self.improvement_expand
            and confidence is not None
            and confidence >= self.confidence_high
        ):
            return (
                "expand_ranges",
                "strong_confident_improvement",
                "Improvement %.3f and confidence %.3f support a wider search."
                % (improvement, confidence),
            )
        if state.diversity is not None and state.diversity <= self.diversity_low:
            return (
                "increase_exploration",
                "low_diversity",
                "Configuration diversity %.3f is at or below %.3f."
                % (state.diversity, self.diversity_low),
            )

        stagnant = improvement is not None and abs(improvement) <= (
            self.improvement_stagnant
        )
        if (
            stagnant
            and state.successful_trials >= self.reselect_after
            and confidence is not None
            and confidence >= self.confidence_high
        ):
            return (
                "reselect_knobs",
                "confident_stagnation",
                "Search is stagnant despite high surrogate confidence; change knobs.",
            )
        if (
            stagnant
            and state.successful_trials >= self.reflect_after
            and (confidence is None or confidence <= self.confidence_low)
        ):
            return (
                "reflect",
                "uncertain_stagnation",
                "Search is stagnant with insufficient surrogate confidence.",
            )
        return (
            "continue_local",
            "default",
            "No intervention threshold was met; continue local optimization.",
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable checkpoint."""

        return {
            "version": self._SERIAL_VERSION,
            "settings": {
                name: getattr(self, name) for name in self._SETTING_NAMES
            },
            "trial_history": [trial.to_dict() for trial in self.trial_history],
            "state_history": [state.to_dict() for state in self.state_history],
            "action_history": [
                action.to_dict() for action in self.action_history
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionController":
        """Restore a checkpoint produced by :meth:`to_dict`."""

        if data.get("version") != cls._SERIAL_VERSION:
            raise ValueError("unsupported decision controller checkpoint version")
        controller = cls(**dict(data.get("settings", {})))
        controller.trial_history = [
            TrialResult.from_dict(item) for item in data.get("trial_history", [])
        ]
        controller.state_history = [
            ControllerState.from_dict(item)
            for item in data.get("state_history", [])
        ]
        controller.action_history = [
            ControllerAction.from_dict(item)
            for item in data.get("action_history", [])
        ]
        return controller

