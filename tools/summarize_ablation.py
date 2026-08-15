#!/usr/bin/env python3
"""Summarize AgentTune ablation runs as CSV and JSON."""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import math
from pathlib import Path
import random
from typing import Any, Iterator, Mapping, Sequence


CSV_FIELDS = (
    "variant",
    "seed",
    "workload",
    "best_throughput",
    "evaluations",
    "evals_to_95pct",
    "failures",
    "failure_rate",
    "wall_time_seconds",
    "total_tokens",
    "total_calls",
    "unique_configs",
    "returncode",
    "record_dir",
)


def iter_json_values(text: str) -> Iterator[Any]:
    """Decode JSONL or concatenated/pretty-printed JSON, skipping bad fragments."""
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        while position < len(text) and (text[position].isspace() or text[position] == ","):
            position += 1
        if position >= len(text):
            return
        try:
            value, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            candidates = []
            for index in range(position + 1, len(text)):
                if text[index] not in "{[":
                    continue
                line_start = text.rfind("\n", position + 1, index) + 1
                if not text[line_start:index].strip():
                    candidates.append(index)
            if not candidates:
                return
            position = min(candidates)
            continue
        yield value
        position = end


def read_json_stream(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    values: list[Any] = []
    for value in iter_json_values(text):
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def read_json_object(path: Path) -> dict[str, Any]:
    values = read_json_stream(path)
    for value in reversed(values):
        if isinstance(value, dict):
            return value
    return {}


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nested_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from nested_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_mappings(child)


def first_number(value: Any, keys: Sequence[str]) -> float | None:
    for mapping in nested_mappings(value):
        for key in keys:
            if key in mapping:
                number = finite_number(mapping[key])
                if number is not None:
                    return number
    return None


def token_metrics(records: Sequence[Any], checkpoint: Mapping[str, Any]) -> tuple[int, int]:
    """Return token and call totals, preferring per-call token logs."""
    tokens = 0.0
    explicit_call_sum = 0.0
    request_counts: list[float] = []
    valid_records = 0
    for record in records:
        if not isinstance(record, Mapping):
            continue
        valid_records += 1
        total = first_number(record, ("total_tokens", "total_token_count", "tokens", "total"))
        if total is None:
            prompt = first_number(record, ("prompt_tokens", "input_tokens", "prompt"))
            completion = first_number(
                record, ("completion_tokens", "output_tokens", "completion")
            )
            if prompt is not None or completion is not None:
                total = (prompt or 0.0) + (completion or 0.0)
        tokens += max(0.0, total or 0.0)
        calls = first_number(record, ("call_count", "calls"))
        if calls is not None:
            explicit_call_sum += max(0.0, calls)
        request_count = first_number(record, ("request_count",))
        if request_count is not None:
            request_counts.append(max(0.0, request_count))

    if records:
        calls = explicit_call_sum or (max(request_counts) if request_counts else valid_records)
        return int(tokens), int(calls)

    checkpoint_tokens = first_number(
        checkpoint, ("total_tokens", "total_token_count", "tokens", "total")
    )
    checkpoint_calls = first_number(checkpoint, ("total_calls", "call_count", "request_count"))
    return int(max(0.0, checkpoint_tokens or 0.0)), int(max(0.0, checkpoint_calls or 0.0))


def canonical_config(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(sorted(value.items(), key=lambda item: str(item[0])))


def infer_identity(record_dir: Path, metadata: Mapping[str, Any]) -> tuple[str, Any, str]:
    variant = str(metadata.get("variant", ""))
    seed: Any = metadata.get("seed", "")
    workload = str(metadata.get("workload", ""))
    parts = record_dir.parts
    if not variant and len(parts) >= 3:
        variant = parts[-3]
    if seed == "" and len(parts) >= 2:
        seed_text = parts[-2]
        if seed_text.startswith("seed-"):
            seed_text = seed_text[5:]
        try:
            seed = int(seed_text)
        except ValueError:
            seed = seed_text
    if not workload:
        workload = record_dir.name
    return variant, seed, workload


def summarize_run(record_dir: Path) -> dict[str, Any]:
    metadata = read_json_object(record_dir / "run_metadata.json")
    checkpoint = read_json_object(record_dir / "checkpoint.json")
    history_path = record_dir / "benmark_history"
    if not history_path.exists():
        history_path = record_dir / "benchmark_history"
    history = [item for item in read_json_stream(history_path) if isinstance(item, Mapping)]

    throughputs = [finite_number(item.get("throughput")) for item in history]
    successful = [value for value in throughputs if value is not None and value > 0]
    checkpoint_best = finite_number(checkpoint.get("best_throughput"))
    best = max([0.0] + successful + ([checkpoint_best] if checkpoint_best is not None else []))
    threshold = best * 0.95
    evals_to_95 = (
        next(
            (
                index
                for index, value in enumerate(throughputs, 1)
                if value is not None and value >= threshold
            ),
            None,
        )
        if best > 0
        else None
    )
    failures = sum(value is None or value <= 0 for value in throughputs)
    unique = {
        encoded
        for item in history
        if (encoded := canonical_config(item.get("knob"))) is not None
    }

    token_path = next(
        (
            candidate
            for name in ("token_usage.jsonl", "token_usage.json", "token_usage")
            if (candidate := record_dir / name).is_file()
        ),
        record_dir / "token_usage.jsonl",
    )
    if not token_path.exists():
        configured = record_dir / "config.ini"
        if configured.is_file():
            parser = configparser.ConfigParser()
            try:
                parser.read(configured, encoding="utf-8")
                candidate = Path(
                    parser["configuration recommender"].get("token_log_path", "")
                )
                if candidate.is_file():
                    token_path = candidate
            except (OSError, KeyError, configparser.Error):
                pass
    tokens, calls = token_metrics(read_json_stream(token_path), checkpoint)
    variant, seed, workload = infer_identity(record_dir, metadata)
    evaluations = len(history)
    wall_time = finite_number(metadata.get("wall_time_seconds"))
    return {
        "variant": variant,
        "seed": seed,
        "workload": workload,
        "best_throughput": best,
        "evaluations": evaluations,
        "evals_to_95pct": evals_to_95,
        "failures": failures,
        "failure_rate": failures / evaluations if evaluations else None,
        "wall_time_seconds": wall_time,
        "total_tokens": tokens,
        "total_calls": calls,
        "unique_configs": len(unique),
        "returncode": metadata.get("returncode"),
        "record_dir": str(record_dir.resolve()),
    }


def discover_runs(root: Path) -> list[Path]:
    markers = (
        "config.ini",
        "run_metadata.json",
        "benmark_history",
        "benchmark_history",
        "checkpoint.json",
    )
    if any((root / marker).is_file() for marker in markers[1:]):
        return [root]
    return sorted(
        {
            path.parent
            for marker in markers
            for path in root.rglob(marker)
            if path.parent != root
        }
    )


def percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot take a percentile of no values")
    position = (len(sorted_values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    left_variant: str,
    right_variant: str,
    iterations: int = 10_000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, Any]:
    """Bootstrap paired right-minus-left final best-throughput differences."""
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    by_variant: dict[str, dict[tuple[Any, Any], float]] = {}
    for row in rows:
        throughput = finite_number(row.get("best_throughput"))
        if throughput is None:
            continue
        key = (row.get("seed"), row.get("workload"))
        by_variant.setdefault(str(row.get("variant")), {})[key] = throughput
    left = by_variant.get(left_variant, {})
    right = by_variant.get(right_variant, {})
    keys = sorted(set(left).intersection(right), key=repr)
    differences = [right[key] - left[key] for key in keys]
    result: dict[str, Any] = {
        "left_variant": left_variant,
        "right_variant": right_variant,
        "difference": "right_minus_left",
        "pairs": len(differences),
        "mean_difference": None,
        "confidence": confidence,
        "ci_low": None,
        "ci_high": None,
    }
    if not differences:
        return result
    result["mean_difference"] = sum(differences) / len(differences)
    generator = random.Random(seed)
    means = sorted(
        sum(generator.choice(differences) for _ in differences) / len(differences)
        for _ in range(iterations)
    )
    alpha = (1 - confidence) / 2
    result["ci_low"] = percentile(means, alpha)
    result["ci_high"] = percentile(means, 1 - alpha)
    return result


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("ablation_runs"))
    parser.add_argument("--csv", type=Path, default=Path("ablation_metrics.csv"))
    parser.add_argument("--json", type=Path, default=Path("ablation_metrics.json"))
    parser.add_argument(
        "--compare",
        nargs=2,
        action="append",
        metavar=("LEFT", "RIGHT"),
        help="Add a paired right-minus-left bootstrap comparison",
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=10_000)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = [summarize_run(run) for run in discover_runs(args.root)]
    rows.sort(key=lambda row: (str(row["variant"]), str(row["workload"]), str(row["seed"])))
    comparisons = args.compare
    if comparisons is None:
        variants = sorted({str(row["variant"]) for row in rows} - {"baseline"})
        comparisons = [["baseline", variant] for variant in variants]
    bootstrap = [
        paired_bootstrap(
            rows,
            left,
            right,
            args.bootstrap_iterations,
            args.confidence,
            args.bootstrap_seed,
        )
        for left, right in comparisons
    ]
    write_csv(args.csv, rows)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps({"runs": rows, "paired_bootstrap": bootstrap}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"summarized {len(rows)} runs into {args.csv} and {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
