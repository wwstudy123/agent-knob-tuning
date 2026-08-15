"""Focused stdlib tests for summarize_ablation.py."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import summarize_ablation


class JsonStreamTests(unittest.TestCase):
    def test_reads_pretty_concatenated_json_jsonl_and_bad_fragment(self) -> None:
        text = (
            '{\n  "throughput": 1,\n  "knob": {"a": 1}\n}\n'
            '{"throughput": 2, "knob": {"a": 2}}\n'
            'this is malformed {"throughput": 999}\n'
            '[{"throughput": 3, "knob": {"a": 3}}]\n'
        )
        values = list(summarize_ablation.iter_json_values(text))
        self.assertEqual(values[0]["throughput"], 1)
        self.assertEqual(values[1]["throughput"], 2)
        self.assertEqual(values[2][0]["throughput"], 3)


class SummaryTests(unittest.TestCase):
    def test_summarizes_history_checkpoint_tokens_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "full" / "seed-7" / "tpcds"
            record.mkdir(parents=True)
            history = [
                {"throughput": 0, "knob": {"a": 1}},
                {"throughput": 80, "knob": {"a": 2}},
                {"throughput": 100, "knob": {"a": 2}},
                {"throughput": "bad", "knob": {"a": 3}},
            ]
            with (record / "benmark_history").open("w", encoding="utf-8") as handle:
                for item in history:
                    json.dump(item, handle, indent=4)
                    handle.write("\n")
            (record / "checkpoint.json").write_text(
                json.dumps({"best_throughput": 100, "request_count": 9}),
                encoding="utf-8",
            )
            (record / "token_usage.jsonl").write_text(
                '{"usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n'
                '{"total_tokens": 20}\n',
                encoding="utf-8",
            )
            (record / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "variant": "full",
                        "seed": 7,
                        "workload": "tpcds",
                        "wall_time_seconds": 12.5,
                        "returncode": 0,
                    }
                ),
                encoding="utf-8",
            )

            result = summarize_ablation.summarize_run(record)

            self.assertEqual(result["best_throughput"], 100)
            self.assertEqual(result["evals_to_95pct"], 3)
            self.assertEqual(result["evaluations"], 4)
            self.assertEqual(result["failures"], 2)
            self.assertEqual(result["failure_rate"], 0.5)
            self.assertEqual(result["unique_configs"], 3)
            self.assertEqual(result["total_tokens"], 35)
            self.assertEqual(result["total_calls"], 2)
            self.assertEqual(result["wall_time_seconds"], 12.5)

    def test_checkpoint_is_token_fallback(self) -> None:
        checkpoint = {
            "request_count": 4,
            "reflection_memory": {"token_usage": {"prompt": 20, "completion": 5, "total": 27}},
        }
        self.assertEqual(summarize_ablation.token_metrics([], checkpoint), (27, 4))


class BootstrapTests(unittest.TestCase):
    def test_paired_bootstrap_uses_only_matching_seed_workload_pairs(self) -> None:
        rows = [
            {"variant": "baseline", "seed": 1, "workload": "job", "best_throughput": 10},
            {"variant": "full", "seed": 1, "workload": "job", "best_throughput": 13},
            {"variant": "baseline", "seed": 2, "workload": "job", "best_throughput": 20},
            {"variant": "full", "seed": 2, "workload": "job", "best_throughput": 25},
            {"variant": "full", "seed": 3, "workload": "job", "best_throughput": 100},
        ]

        result = summarize_ablation.paired_bootstrap(
            rows, "baseline", "full", iterations=200, seed=42
        )

        self.assertEqual(result["pairs"], 2)
        self.assertEqual(result["mean_difference"], 4)
        self.assertGreaterEqual(result["ci_low"], 3)
        self.assertLessEqual(result["ci_high"], 5)


if __name__ == "__main__":
    unittest.main()
