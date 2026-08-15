"""Focused stdlib tests for run_ablation.py."""

from __future__ import annotations

import configparser
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_ablation


MINIMAL_CONFIG = """\
[workload analyzer]
workload_file = original.sql

[configuration recommender]
random_seed = 1
benchmark = OLD
adaptive_controller_enabled = true
surrogate_enabled = true
reflection_enabled = true
record_dir = old
"""


class ConfigTests(unittest.TestCase):
    def test_build_config_applies_variant_and_run_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.ini"
            destination = root / "run" / "config.ini"
            record_dir = root / "records"
            source.write_text(MINIMAL_CONFIG, encoding="utf-8")

            run_ablation.build_config(
                source,
                destination,
                "surrogate",
                17,
                "tpcds",
                "queries/tpcds.sql",
                record_dir,
            )

            parser = configparser.ConfigParser()
            parser.read(destination, encoding="utf-8")
            section = parser["configuration recommender"]
            self.assertFalse(section.getboolean("adaptive_controller_enabled"))
            self.assertTrue(section.getboolean("surrogate_enabled"))
            self.assertFalse(section.getboolean("reflection_enabled"))
            self.assertEqual(section.getint("random_seed"), 17)
            self.assertEqual(section["benchmark"], "TPCDS")
            self.assertEqual(section["run_mode"], "fresh")
            self.assertEqual(Path(section["record_dir"]), record_dir.resolve())
            self.assertEqual(
                parser["workload analyzer"]["workload_file"], "queries/tpcds.sql"
            )

    def test_dry_run_preserves_config_and_writes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.ini"
            record_dir = root / "baseline" / "seed-3" / "job"
            config_path = record_dir / "config.ini"
            source.write_text(MINIMAL_CONFIG, encoding="utf-8")
            run_ablation.build_config(
                source, config_path, "baseline", 3, "job", None, record_dir
            )

            returncode = run_ablation.execute_run(
                root,
                ["not-a-real-command"],
                config_path,
                record_dir,
                "baseline",
                3,
                "job",
                True,
            )

            self.assertEqual(returncode, 0)
            self.assertTrue(config_path.is_file())
            metadata = json.loads((record_dir / "run_metadata.json").read_text())
            self.assertTrue(metadata["dry_run"])
            self.assertIsNone(metadata["returncode"])
            self.assertFalse((record_dir / "stdout.log").exists())

    def test_execute_sets_config_environment_and_captures_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_dir = root / "record"
            config_path = record_dir / "config.ini"
            record_dir.mkdir()
            config_path.write_text(MINIMAL_CONFIG, encoding="utf-8")
            code = "import os; print(os.environ['AGENTTUNE_CONFIG'])"

            returncode = run_ablation.execute_run(
                root,
                [sys.executable, "-c", code],
                config_path,
                record_dir,
                "full",
                5,
                "tpcds",
                False,
            )

            self.assertEqual(returncode, 0)
            self.assertEqual(
                (record_dir / "stdout.log").read_text(encoding="utf-8").strip(),
                str(config_path.resolve()),
            )
            metadata = json.loads((record_dir / "run_metadata.json").read_text())
            self.assertEqual(metadata["returncode"], 0)
            self.assertGreaterEqual(metadata["wall_time_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
