#!/usr/bin/env python3
"""Run reproducible AgentTune ablation experiments."""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Iterable, Sequence


VARIANTS = {
    "baseline": (False, False, False),
    "controller": (True, False, False),
    "surrogate": (False, True, False),
    "reflection": (False, False, True),
    "full": (True, True, True),
}


def safe_component(value: object) -> str:
    """Return a filesystem-safe, readable run identifier component."""
    text = str(value).strip()
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in text)
    return cleaned.strip("._") or "unnamed"


def parse_workload(spec: str) -> tuple[str, str | None]:
    """Parse NAME or NAME=WORKLOAD_FILE."""
    name, separator, path = spec.partition("=")
    name = name.strip()
    if not name:
        raise ValueError(f"invalid workload specification: {spec!r}")
    if separator and not path.strip():
        raise ValueError(f"workload path is empty: {spec!r}")
    return name, path.strip() if separator else None


def command_argv(command: str) -> list[str]:
    """Split a command consistently on POSIX and Windows."""
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        argv = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
            else token
            for token in argv
        ]
    if not argv:
        raise ValueError("command must not be empty")
    return argv


def build_config(
    source: Path,
    destination: Path,
    variant: str,
    seed: int,
    workload: str,
    workload_file: str | None,
    record_dir: Path,
) -> None:
    """Create and preserve one run-specific config derived from source."""
    parser = configparser.ConfigParser()
    if not parser.read(source, encoding="utf-8"):
        raise FileNotFoundError(source)
    section = parser["configuration recommender"]
    controller, surrogate, reflection = VARIANTS[variant]
    section["adaptive_controller_enabled"] = str(controller).lower()
    section["surrogate_enabled"] = str(surrogate).lower()
    section["reflection_enabled"] = str(reflection).lower()
    section["random_seed"] = str(seed)
    section["benchmark"] = workload.upper()
    section["run_mode"] = "fresh"
    section["record_dir"] = str(record_dir.resolve())
    section["token_log_path"] = str((record_dir / "token_usage.jsonl").resolve())
    if workload_file is not None:
        parser["workload analyzer"]["workload_file"] = workload_file

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    temporary.replace(destination)


def iter_runs(
    variants: Iterable[str], seeds: Iterable[int], workloads: Iterable[str]
) -> Iterable[tuple[str, int, str, str | None]]:
    for variant in variants:
        for seed in seeds:
            for workload_spec in workloads:
                workload, workload_file = parse_workload(workload_spec)
                yield variant, seed, workload, workload_file


def execute_run(
    repo: Path,
    command: Sequence[str],
    config_path: Path,
    record_dir: Path,
    variant: str,
    seed: int,
    workload: str,
    dry_run: bool,
) -> int:
    """Execute one run and persist logs plus timing metadata."""
    record_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = record_dir / "run_metadata.json"
    metadata = {
        "variant": variant,
        "seed": seed,
        "workload": workload,
        "config": str(config_path.resolve()),
        "record_dir": str(record_dir.resolve()),
        "command": list(command),
        "dry_run": dry_run,
        "started_at": None,
        "finished_at": None,
        "wall_time_seconds": None,
        "returncode": None,
    }
    if dry_run:
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"DRY RUN {variant} seed={seed} workload={workload}: {shlex.join(command)}")
        return 0

    environment = os.environ.copy()
    environment["AGENTTUNE_CONFIG"] = str(config_path.resolve())
    started_wall = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    metadata["started_at"] = started_wall.isoformat()
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    try:
        with (record_dir / "stdout.log").open("w", encoding="utf-8") as stdout, (
            record_dir / "stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            result = subprocess.run(
                list(command),
                cwd=repo,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        returncode = result.returncode
    except OSError as error:
        returncode = 127
        (record_dir / "stderr.log").write_text(f"{error}\n", encoding="utf-8")

    metadata["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    metadata["wall_time_seconds"] = time.monotonic() - started
    metadata["returncode"] = returncode
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.ini"))
    parser.add_argument("--output-root", type=Path, default=Path("ablation_runs"))
    parser.add_argument("--variants", nargs="+", choices=tuple(VARIANTS), default=list(VARIANTS))
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument(
        "--workloads",
        nargs="+",
        required=True,
        metavar="NAME[=FILE]",
        help="Benchmark name, optionally paired with its workload file",
    )
    parser.add_argument("--command", default="bash run.bash")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after a run command exits unsuccessfully",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    config = args.config if args.config.is_absolute() else repo / args.config
    output_root = args.output_root if args.output_root.is_absolute() else repo / args.output_root
    command = command_argv(args.command)

    failures = 0
    for variant, seed, workload, workload_file in iter_runs(
        args.variants, args.seeds, args.workloads
    ):
        record_dir = (
            output_root
            / safe_component(variant)
            / f"seed-{safe_component(seed)}"
            / safe_component(workload)
        )
        config_path = record_dir / "config.ini"
        build_config(
            config,
            config_path,
            variant,
            seed,
            workload,
            workload_file,
            record_dir,
        )
        returncode = execute_run(
            repo,
            command,
            config_path,
            record_dir,
            variant,
            seed,
            workload,
            args.dry_run,
        )
        if returncode:
            failures += 1
            print(
                f"run failed ({returncode}): {variant} seed={seed} workload={workload}",
                file=sys.stderr,
            )
            if not args.keep_going:
                return returncode
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
