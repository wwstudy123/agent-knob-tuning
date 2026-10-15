"""Dynamic search-space control for database knob tuning.

Wraps the static knob metadata produced by the range pruner and lets the
adaptive decision controller shrink or expand the searchable ranges around a
centre configuration (typically the current best).  The object is JSON
serialisable so its state can be checkpointed and restored across runs.

The module has no dependency on the rest of the recommender, which keeps it
cheap to unit-test and safe to import from either process.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Mapping, Optional


_COMPACT_FIELDS = (
    "min_value",
    "max_value",
    "step",
    "type",
    "enum_values",
    "special_value",
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _maybe_int(value: float) -> Any:
    f = float(value)
    return int(f) if f.is_integer() else f


class SearchSpace:
    """A mutable view over knob ranges with original-bound clamping."""

    def __init__(self, knob_metadata: Mapping[str, Any]) -> None:
        if not isinstance(knob_metadata, Mapping):
            raise TypeError("knob_metadata must be a mapping")
        self._original: Dict[str, Dict[str, Any]] = {}
        self._current: Dict[str, Dict[str, Any]] = {}
        for key, meta in knob_metadata.items():
            original_meta = copy.deepcopy(dict(meta or {}))
            self._original[key] = original_meta
            self._current[key] = copy.deepcopy(original_meta)

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def metadata(self) -> Dict[str, Dict[str, Any]]:
        """Current (possibly pruned) knob metadata, deep-copied."""
        return copy.deepcopy(self._current)

    def original(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self._original)

    def active_knobs(self) -> List[str]:
        return sorted(self._current.keys())

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------
    def shrink(
        self, center: Optional[Mapping[str, Any]], ratio: float = 0.5
    ) -> Dict[str, Any]:
        """Narrow each numeric range toward ``center``.

        ``ratio`` is the fraction of the current span retained around the
        centre (0 < ratio < 1).  Enum knobs and knobs without numeric bounds
        are skipped.  Returns a change report.
        """
        return self._rescale(dict(center or {}), ratio, shrink=True)

    def expand(self, ratio: float = 0.25) -> Dict[str, Any]:
        """Widen numeric ranges back toward the original boundaries.

        ``ratio`` is the fraction of the original span added on each side.
        """
        return self._rescale(None, ratio, shrink=False)

    def reset(self) -> None:
        """Restore every knob to its original range."""
        for key, meta in self._original.items():
            self._current[key] = copy.deepcopy(meta)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def compact_json(self) -> str:
        """Compact JSON of current ranges, matching LLM_server's prompt shape."""
        return json.dumps(
            {
                key: {
                    field: value
                    for field, value in meta.items()
                    if field in _COMPACT_FIELDS
                }
                for key, meta in self._current.items()
            },
            separators=(",", ":"),
        )

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original": copy.deepcopy(self._original),
            "current": copy.deepcopy(self._current),
        }

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "SearchSpace":
        if not data or "original" not in data or "current" not in data:
            raise ValueError("invalid search space payload")
        obj = cls.__new__(cls)
        obj._original = {
            k: copy.deepcopy(dict(v)) for k, v in data["original"].items()
        }
        obj._current = {
            k: copy.deepcopy(dict(v)) for k, v in data["current"].items()
        }
        return obj

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _rescale(
        self,
        center: Optional[Dict[str, Any]],
        ratio: float,
        shrink: bool,
    ) -> Dict[str, Any]:
        ratio = float(ratio)
        if not 0.0 < ratio < 1.0:
            raise ValueError("ratio must be between 0 and 1")

        report: Dict[str, Any] = {
            "shrink": bool(shrink),
            "ratio": ratio,
            "changed": [],
            "skipped": [],
        }

        for key, meta in self._current.items():
            if meta.get("type") == "enum":
                report["skipped"].append(key)
                continue
            lo = meta.get("min_value")
            hi = meta.get("max_value")
            if not _is_number(lo) or not _is_number(hi):
                report["skipped"].append(key)
                continue

            orig = self._original[key]
            orig_lo = orig.get("min_value", lo)
            orig_hi = orig.get("max_value", hi)
            if not (_is_number(orig_lo) and _is_number(orig_hi)):
                orig_lo, orig_hi = lo, hi

            if shrink:
                c = center.get(key) if center else None
                if not _is_number(c):
                    report["skipped"].append(key)
                    continue
                c = min(max(float(c), float(lo)), float(hi))
                new_lo = c - (c - float(lo)) * ratio
                new_hi = c + (float(hi) - c) * ratio
                # never exceed the original bounds
                new_lo = max(new_lo, float(orig_lo))
                new_hi = min(new_hi, float(orig_hi))
            else:
                grow = (float(orig_hi) - float(orig_lo)) * ratio
                new_lo = max(float(orig_lo), float(lo) - grow)
                new_hi = min(float(orig_hi), float(hi) + grow)

            if meta.get("type") == "integer":
                rlo, rhi = int(round(new_lo)), int(round(new_hi))
                if rlo > rhi:
                    report["skipped"].append(key)
                    continue
                new_lo, new_hi = rlo, rhi
            else:
                new_lo, new_hi = _maybe_int(new_lo), _maybe_int(new_hi)

            if not (float(new_lo) <= float(new_hi)):
                report["skipped"].append(key)
                continue

            meta["min_value"] = new_lo
            meta["max_value"] = new_hi
            report["changed"].append(key)

        return report
