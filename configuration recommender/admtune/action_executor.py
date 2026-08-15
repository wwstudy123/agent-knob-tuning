"""Lightweight executable hooks for ADMTune actions (smoke-friendly)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ActionResult:
    action: str
    applied: bool
    detail: str
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "applied": self.applied,
            "detail": self.detail,
            "artifacts": dict(self.artifacts),
        }


class ActionExecutor:
    """Apply side effects for Reselect/Reanalyze/AdjustRange.

    For smoke validation this records intent and optionally shrinks/expands
    numeric ranges in pruned_knobs JSON. Full LLM re-select/re-analyze can be
    layered later without changing the ADMTune loop contract.
    """

    def __init__(
        self,
        *,
        pruned_knobs_path: Optional[str] = None,
        selected_knobs_path: Optional[str] = None,
        workload_features_path: Optional[str] = None,
        record_dir: Optional[str] = None,
    ) -> None:
        self.pruned_knobs_path = Path(pruned_knobs_path) if pruned_knobs_path else None
        self.selected_knobs_path = Path(selected_knobs_path) if selected_knobs_path else None
        self.workload_features_path = (
            Path(workload_features_path) if workload_features_path else None
        )
        self.record_dir = Path(record_dir) if record_dir else None

    def apply(self, action: str, reason: str = "") -> ActionResult:
        action = str(action)
        if action == "Execute":
            return ActionResult(action, True, "no structural change; proceed to propose/evaluate")
        if action == "Stop":
            return ActionResult(action, True, "stop search")
        if action == "Reselect":
            note = {
                "action": "Reselect",
                "reason": reason,
                "selected_knobs": str(self.selected_knobs_path),
            }
            self._append_event(note)
            return ActionResult(
                action,
                True,
                "marked Reselect; next proposals should explore alternate knobs",
                note,
            )
        if action == "Reanalyze":
            digest = None
            if self.workload_features_path and self.workload_features_path.exists():
                digest = self.workload_features_path.read_text(encoding="utf-8", errors="ignore")
                digest = str(hash(digest[:4000]))
            note = {
                "action": "Reanalyze",
                "reason": reason,
                "workload_digest": digest,
            }
            self._append_event(note)
            return ActionResult(
                action,
                True,
                "marked Reanalyze; workload features digest refreshed for Omega_t",
                note,
            )
        if action == "AdjustRange":
            changed = self._nudge_ranges(shrink="fail" in reason.lower() or "elevated" in reason.lower())
            note = {"action": "AdjustRange", "reason": reason, "changed": changed}
            self._append_event(note)
            return ActionResult(
                action,
                True,
                f"adjusted numeric ranges ({'shrink' if changed.get('mode')=='shrink' else 'expand'})",
                note,
            )
        return ActionResult(action, False, f"unknown action {action}")

    def _append_event(self, note: Dict[str, Any]) -> None:
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        path = self.record_dir / "action_events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(note, ensure_ascii=False) + "\n")

    def _nudge_ranges(self, shrink: bool = True) -> Dict[str, Any]:
        if not self.pruned_knobs_path or not self.pruned_knobs_path.exists():
            return {"mode": "shrink" if shrink else "expand", "updated": 0}
        try:
            data = json.loads(self.pruned_knobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"mode": "shrink" if shrink else "expand", "updated": 0, "error": "read_failed"}
        updated = 0
        factor = 0.9 if shrink else 1.1
        for _name, meta in list(data.items())[:8]:
            if not isinstance(meta, dict):
                continue
            if meta.get("type") == "enum":
                continue
            try:
                lo = float(meta.get("min_value", meta.get("min")))
                hi = float(meta.get("max_value", meta.get("max")))
            except (TypeError, ValueError):
                continue
            if hi <= lo:
                continue
            mid = 0.5 * (lo + hi)
            half = 0.5 * (hi - lo) * factor
            new_lo, new_hi = mid - half, mid + half
            if "min_value" in meta:
                meta["min_value"] = new_lo
                meta["max_value"] = new_hi
            else:
                meta["min"] = new_lo
                meta["max"] = new_hi
            updated += 1
        if updated:
            self.pruned_knobs_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return {"mode": "shrink" if shrink else "expand", "updated": updated}
