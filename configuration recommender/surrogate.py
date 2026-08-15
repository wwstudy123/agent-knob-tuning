"""Lightweight surrogate modelling for MySQL configuration search.

This module deliberately has no dependency on the rest of the recommender.
Scikit-learn is optional: when it is unavailable (or there is too little
history), predictions use transparent empirical priors.
"""

from __future__ import annotations

import json
import math
import os
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:  # Keep importing this module useful in minimal benchmark environments.
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

    SKLEARN_AVAILABLE = True
except Exception:  # pragma: no cover - optional/binary dependency is environment dependent
    np = None
    RandomForestClassifier = None
    GaussianProcessRegressor = None
    ConstantKernel = Matern = WhiteKernel = None
    SKLEARN_AVAILABLE = False


JSONSource = Union[str, os.PathLike, Mapping[str, Mapping[str, Any]]]


def _read_metadata(source: Optional[JSONSource]) -> Dict[str, Dict[str, Any]]:
    if source is None:
        return {}
    if isinstance(source, Mapping):
        return {str(key): dict(value) for key, value in source.items()}
    with open(os.fspath(source), "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("knob metadata must be a JSON object")
    return {str(key): dict(value) for key, value in data.items()}


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class CanonicalConfigEncoder:
    """Canonicalize configurations and encode them into stable numeric vectors.

    Candidate metadata establishes the real-name <-> ``knobN`` mapping.
    Pruned metadata, when supplied, selects knobs and overrides their ranges.
    Numeric values are normalized in log1p space; enums are one-hot encoded.
    """

    def __init__(
        self,
        candidate_metadata: JSONSource,
        pruned_metadata: Optional[JSONSource] = None,
    ) -> None:
        self.candidate_metadata = _read_metadata(candidate_metadata)
        self.pruned_metadata = _read_metadata(pruned_metadata)
        self.mysql_to_knob = {
            mysql_name: "knob{}".format(index)
            for index, mysql_name in enumerate(self.candidate_metadata, start=1)
        }
        self.knob_to_mysql = {value: key for key, value in self.mysql_to_knob.items()}

        if self.pruned_metadata:
            normalized_pruned: Dict[str, Dict[str, Any]] = {}
            for key, value in self.pruned_metadata.items():
                normalized_pruned[self.mysql_to_knob.get(key, key)] = value
            self.pruned_metadata = normalized_pruned
            selected = list(self.pruned_metadata)
        else:
            selected = list(self.mysql_to_knob.values())
        self.knob_names = sorted(
            selected,
            key=lambda name: (
                0,
                int(name[4:]),
            )
            if name.startswith("knob") and name[4:].isdigit()
            else (1, name),
        )
        self.metadata: Dict[str, Dict[str, Any]] = {}
        self.enum_values: Dict[str, List[str]] = {}
        self.feature_names: List[str] = []

        for knob in self.knob_names:
            mysql_name = self.knob_to_mysql.get(knob, knob)
            combined = dict(self.candidate_metadata.get(mysql_name, {}))
            combined.update(self.pruned_metadata.get(knob, {}))
            self.metadata[knob] = combined
            if combined.get("type") == "enum":
                values = self._enum_domain(knob, combined)
                self.enum_values[knob] = values
                self.feature_names.extend("{}={}".format(knob, value) for value in values)
            else:
                self.feature_names.append(knob)

    def _enum_domain(self, knob: str, metadata: Mapping[str, Any]) -> List[str]:
        mysql_name = self.knob_to_mysql.get(knob, knob)
        values = (
            metadata.get("enum_values")
            or self.candidate_metadata.get(mysql_name, {}).get("enum_values")
            or []
        )
        if not values:
            lower = metadata.get("min_value", metadata.get("min"))
            upper = metadata.get("max_value", metadata.get("max"))
            if isinstance(lower, int) and isinstance(upper, int) and 0 <= upper - lower <= 64:
                values = list(range(lower, upper + 1))
        return [str(value) for value in values]

    def canonical_key(self, key: str) -> Optional[str]:
        key = str(key)
        if key in self.metadata:
            return key
        mapped = self.mysql_to_knob.get(key)
        return mapped if mapped in self.metadata else None

    def canonicalize(
        self, config: Mapping[str, Any], include_missing: bool = False
    ) -> Dict[str, Any]:
        """Return a ``knobN`` keyed configuration.

        Unknown and unselected knobs are ignored. If both forms occur, the
        explicit ``knobN`` value wins regardless of input dictionary order.
        """
        result: Dict[str, Any] = {}
        for key, value in config.items():
            canonical = self.canonical_key(str(key))
            if canonical is not None and not str(key).startswith("knob"):
                result[canonical] = value
        for key, value in config.items():
            canonical = self.canonical_key(str(key))
            if canonical is not None and str(key).startswith("knob"):
                result[canonical] = value
        if include_missing:
            for knob in self.knob_names:
                result.setdefault(knob, self.default_value(knob))
        return {knob: result[knob] for knob in self.knob_names if knob in result}

    canonical_config = canonicalize

    def default_value(self, knob: str) -> Any:
        metadata = self.metadata[knob]
        if metadata.get("type") == "enum":
            values = self.enum_values.get(knob, [])
            return values[0] if values else None
        return metadata.get("min_value", metadata.get("min", 0))

    def _numeric_feature(self, knob: str, raw_value: Any) -> float:
        metadata = self.metadata[knob]
        lower = _as_float(metadata.get("min_value", metadata.get("min", 0)))
        upper = _as_float(metadata.get("max_value", metadata.get("max", lower)))
        value = _as_float(raw_value)
        lower = 0.0 if lower is None else lower
        upper = lower if upper is None else upper
        value = lower if value is None else min(max(value, lower), upper)

        # MySQL tuning ranges are non-negative. Shift only for custom metadata
        # containing negatives so log1p remains defined.
        shift = -min(0.0, lower)
        log_lower = math.log1p(lower + shift)
        log_upper = math.log1p(upper + shift)
        if log_upper <= log_lower:
            return 0.0
        return (math.log1p(value + shift) - log_lower) / (log_upper - log_lower)

    def _canonical_enum(self, knob: str, raw_value: Any) -> Optional[str]:
        values = self.enum_values.get(knob, [])
        text = str(raw_value)
        if text in values:
            return text
        folded = {value.casefold(): value for value in values}
        if text.casefold() in folded:
            return folded[text.casefold()]
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            index = int(raw_value)
            if raw_value == index and 0 <= index < len(values):
                return values[index]
        if text.isdigit() and 0 <= int(text) < len(values):
            return values[int(text)]
        return None

    def encode(self, config: Mapping[str, Any]) -> List[float]:
        canonical = self.canonicalize(config)
        encoded: List[float] = []
        for knob in self.knob_names:
            metadata = self.metadata[knob]
            raw_value = canonical.get(knob, self.default_value(knob))
            if metadata.get("type") == "enum":
                chosen = self._canonical_enum(knob, raw_value)
                encoded.extend(
                    1.0 if value == chosen else 0.0
                    for value in self.enum_values.get(knob, [])
                )
            else:
                encoded.append(self._numeric_feature(knob, raw_value))
        return encoded

    transform_one = encode

    def transform(self, configs: Iterable[Mapping[str, Any]]) -> List[List[float]]:
        return [self.encode(config) for config in configs]

    def diagnostics(self) -> Dict[str, Any]:
        return {
            "knob_count": len(self.knob_names),
            "feature_count": len(self.feature_names),
            "knobs": list(self.knob_names),
            "feature_names": list(self.feature_names),
        }


def _history_config(item: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    for key in ("config", "configuration", "knobs", "knob"):
        value = item.get(key)
        if isinstance(value, Mapping):
            return value
    excluded = {
        "throughput", "success", "failed", "failure", "status", "error",
        "metrics", "timestamp", "duration",
    }
    possible = {key: value for key, value in item.items() if key not in excluded}
    return possible or None


def _history_success(item: Mapping[str, Any], throughput: Optional[float]) -> bool:
    if "success" in item:
        return bool(item["success"])
    if "failed" in item:
        return not bool(item["failed"])
    if "failure" in item:
        return not bool(item["failure"])
    status = str(item.get("status", "")).casefold()
    if status:
        return status in {"success", "succeeded", "ok", "complete", "completed"}
    if item.get("error"):
        return False
    return throughput is not None and throughput > 0.0


def load_history_jsonl(
    path: Union[str, os.PathLike],
    encoder: Optional[CanonicalConfigEncoder] = None,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """Load benchmark JSONL into normalized, serializable records.

    Blank lines are ignored. Invalid records are skipped unless ``strict`` is
    true. A successful record must have a finite, non-negative throughput.
    """
    records: List[Dict[str, Any]] = []
    with open(os.fspath(path), "r", encoding="utf-8") as handle:
        content = handle.read()
    # raw_decode also accepts the repository's legacy history format, which
    # appends pretty-printed JSON objects separated by whitespace.
    decoder = json.JSONDecoder()
    position = 0
    while position < len(content):
        while position < len(content) and content[position].isspace():
            position += 1
        if position >= len(content):
            break
        line_number = content.count("\n", 0, position) + 1
        try:
            item, next_position = decoder.raw_decode(content, position)
            if not isinstance(item, Mapping):
                raise ValueError("record is not an object")
            config = _history_config(item)
            if config is None:
                raise ValueError("record has no configuration")
            throughput = _as_float(item.get("throughput"))
            success = _history_success(item, throughput)
            if success and (throughput is None or throughput < 0):
                raise ValueError("successful record has invalid throughput")
            records.append(
                {
                    "config": (
                        encoder.canonicalize(config) if encoder is not None else dict(config)
                    ),
                    "throughput": throughput,
                    "success": success,
                }
            )
            position = next_position
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if strict:
                raise ValueError(
                    "invalid history record at line {}: {}".format(line_number, exc)
                ) from exc
            newline = content.find("\n", position)
            position = len(content) if newline < 0 else newline + 1
    return records


class SurrogateModel:
    """Failure-aware throughput surrogate with a dependency-free fallback."""

    def __init__(
        self,
        encoder: CanonicalConfigEncoder,
        min_samples: int = 8,
        min_successes: int = 3,
        random_state: int = 0,
        n_estimators: int = 200,
    ) -> None:
        self.encoder = encoder
        self.min_samples = max(1, int(min_samples))
        self.min_successes = max(1, int(min_successes))
        self.random_state = int(random_state)
        self.n_estimators = max(10, int(n_estimators))
        self._classifier = None
        self._regressor = None
        self._classifier_constant: Optional[float] = None
        self._history: List[Dict[str, Any]] = []
        self._success_values: List[float] = []
        self._fit_error: Optional[str] = None

    def fit(self, history: Sequence[Mapping[str, Any]]) -> "SurrogateModel":
        normalized: List[Dict[str, Any]] = []
        for item in history:
            config = _history_config(item)
            if config is None:
                continue
            throughput = _as_float(item.get("throughput"))
            success = _history_success(item, throughput)
            if success and (throughput is None or throughput < 0):
                success = False
            normalized.append(
                {"config": dict(config), "throughput": throughput, "success": success}
            )

        self._history = normalized
        self._success_values = [
            float(item["throughput"])
            for item in normalized
            if item["success"] and item["throughput"] is not None
        ]
        self._classifier = self._regressor = None
        self._fit_error = None
        failures = [0.0 if item["success"] else 1.0 for item in normalized]
        self._classifier_constant = fmean(failures) if failures else 0.5

        if not SKLEARN_AVAILABLE or len(normalized) < self.min_samples:
            return self

        try:
            features = np.asarray(
                self.encoder.transform(item["config"] for item in normalized), dtype=float
            )
            labels = np.asarray(failures, dtype=int)
            if len(set(labels.tolist())) > 1:
                self._classifier = RandomForestClassifier(
                    n_estimators=self.n_estimators,
                    min_samples_leaf=2,
                    class_weight="balanced",
                    random_state=self.random_state,
                    n_jobs=1,
                )
                self._classifier.fit(features, labels)

            successful = [
                (features[index], float(item["throughput"]))
                for index, item in enumerate(normalized)
                if item["success"] and item["throughput"] is not None
            ]
            if len(successful) >= self.min_successes:
                x_success = np.asarray([pair[0] for pair in successful], dtype=float)
                y_success = np.log1p(
                    np.asarray([pair[1] for pair in successful], dtype=float)
                )
                dimensions = max(1, features.shape[1])
                kernel = (
                    ConstantKernel(1.0, (1e-2, 1e2))
                    * Matern(
                        length_scale=np.ones(dimensions),
                        length_scale_bounds=(1e-2, 1e2),
                        nu=2.5,
                    )
                    + WhiteKernel(noise_level=1e-4, noise_level_bounds=(1e-8, 1e0))
                )
                self._regressor = GaussianProcessRegressor(
                    kernel=kernel,
                    normalize_y=True,
                    random_state=self.random_state,
                    n_restarts_optimizer=0,
                )
                self._regressor.fit(x_success, y_success)
        except Exception as exc:  # Degrade rather than break the tuning loop.
            self._classifier = self._regressor = None
            self._fit_error = "{}: {}".format(type(exc).__name__, exc)
        return self

    def fit_jsonl(
        self, path: Union[str, os.PathLike], strict: bool = False
    ) -> "SurrogateModel":
        return self.fit(load_history_jsonl(path, self.encoder, strict=strict))

    def _fallback_throughput(self) -> Tuple[float, float]:
        if not self._success_values:
            return 0.0, 1.0
        mean = fmean(self._success_values)
        std = pstdev(self._success_values) if len(self._success_values) > 1 else max(
            1.0, abs(mean) * 0.25
        )
        return mean, max(std, 1e-9)

    def predict(self, config: Mapping[str, Any]) -> Dict[str, float]:
        features = self.encoder.encode(config)
        failure_probability = (
            0.5
            if self._classifier_constant is None
            else float(self._classifier_constant)
        )
        if self._classifier is not None:
            probabilities = self._classifier.predict_proba(
                np.asarray([features], dtype=float)
            )[0]
            by_class = {
                int(label): float(probability)
                for label, probability in zip(self._classifier.classes_, probabilities)
            }
            failure_probability = by_class.get(1, 0.0)
        success_probability = min(1.0, max(0.0, 1.0 - failure_probability))

        if self._regressor is None:
            mean, std = self._fallback_throughput()
        else:
            log_mean, log_std = self._regressor.predict(
                np.asarray([features], dtype=float), return_std=True
            )
            mu, sigma = float(log_mean[0]), max(0.0, float(log_std[0]))
            variance = sigma * sigma
            mean = max(0.0, math.exp(mu + 0.5 * variance) - 1.0)
            std = math.sqrt(max(0.0, (math.exp(variance) - 1.0) * math.exp(
                2.0 * mu + variance
            )))
        return {
            "mean": float(mean),
            "std": float(std),
            "success_probability": float(success_probability),
            "failure_probability": float(1.0 - success_probability),
        }

    predict_one = predict

    def predict_many(
        self, configs: Iterable[Mapping[str, Any]]
    ) -> List[Dict[str, float]]:
        return [self.predict(config) for config in configs]

    @staticmethod
    def _normal_pdf(value: float) -> float:
        return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)

    @staticmethod
    def _normal_cdf(value: float) -> float:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

    def rank_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        top_k: Optional[int] = None,
        strategy: str = "ucb",
        beta: float = 2.0,
        xi: float = 0.01,
        min_success_probability: float = 0.5,
        force_exploration: bool = True,
    ) -> List[Dict[str, Any]]:
        """Rank candidates using constrained UCB or expected improvement.

        Acquisition is multiplied by predicted success probability. Candidates
        below the constraint remain rankable but sort behind feasible ones.
        When a subset is requested, one maximum-uncertainty candidate is forced
        into it (provided at least one slot exists).
        """
        strategy = strategy.casefold()
        if strategy not in {"ucb", "ei"}:
            raise ValueError("strategy must be 'ucb' or 'ei'")
        best = max(self._success_values, default=0.0)
        ranked: List[Dict[str, Any]] = []
        for index, config in enumerate(candidates):
            prediction = self.predict(config)
            mean, std = prediction["mean"], prediction["std"]
            if strategy == "ucb":
                raw = mean + float(beta) * std
            elif std <= 0:
                raw = max(0.0, mean - best - float(xi))
            else:
                improvement = mean - best - float(xi)
                z_value = improvement / std
                raw = improvement * self._normal_cdf(z_value) + std * self._normal_pdf(
                    z_value
                )
            feasible = prediction["success_probability"] >= min_success_probability
            ranked.append(
                {
                    "config": self.encoder.canonicalize(config),
                    "prediction": prediction,
                    "acquisition": float(raw * prediction["success_probability"]),
                    "feasible": bool(feasible),
                    "exploration": False,
                    "_index": index,
                }
            )
        ranked.sort(
            key=lambda item: (
                item["feasible"],
                item["acquisition"],
                item["prediction"]["success_probability"],
                -item["_index"],
            ),
            reverse=True,
        )
        limit = len(ranked) if top_k is None else max(0, min(int(top_k), len(ranked)))
        selected = ranked[:limit]
        if force_exploration and selected:
            uncertain = max(
                ranked,
                key=lambda item: (
                    item["prediction"]["std"],
                    item["prediction"]["failure_probability"]
                    * item["prediction"]["success_probability"],
                    -item["_index"],
                ),
            )
            if all(item["_index"] != uncertain["_index"] for item in selected):
                selected[-1] = uncertain
            uncertain["exploration"] = True
        for item in selected:
            item.pop("_index", None)
        return selected

    rank = rank_candidates

    def diagnostics(self) -> Dict[str, Any]:
        failures = len(self._history) - len(self._success_values)
        if self._regressor is not None:
            mode = "sklearn"
        else:
            mode = "fallback"
        return {
            "backend": "sklearn" if SKLEARN_AVAILABLE else "stdlib",
            "mode": mode,
            "history_samples": len(self._history),
            "successful_samples": len(self._success_values),
            "failed_samples": failures,
            "classifier_fitted": self._classifier is not None,
            "regressor_fitted": self._regressor is not None,
            "empirical_success_probability": (
                len(self._success_values) / len(self._history) if self._history else 0.5
            ),
            "fit_error": self._fit_error,
            "encoder": self.encoder.diagnostics(),
        }


# Short aliases make the standalone module convenient to integrate.
ConfigEncoder = CanonicalConfigEncoder
Surrogate = SurrogateModel

__all__ = [
    "SKLEARN_AVAILABLE",
    "CanonicalConfigEncoder",
    "ConfigEncoder",
    "SurrogateModel",
    "Surrogate",
    "load_history_jsonl",
]
