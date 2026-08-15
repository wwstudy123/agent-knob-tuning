import json
import unittest

from reflection_memory import ReflectionMemory, static_context_digest


class ReflectionMemoryTests(unittest.TestCase):
    def test_initial_state_and_stable_context_hash(self):
        left = ReflectionMemory({"workload": "tpcc", "knobs": ["a", "b"]})
        right = ReflectionMemory({"knobs": ["a", "b"], "workload": "tpcc"})

        self.assertEqual(left.version, 1)
        self.assertEqual(left.static_context_hash, right.static_context_hash)
        self.assertEqual(left.static_context_hash, static_context_digest(
            {"knobs": ["a", "b"], "workload": "tpcc"}
        ))
        self.assertEqual(left.turn, 0)
        self.assertEqual(left.tokens_remaining, 4096)

    def test_trial_delta_is_against_previous_best(self):
        memory = ReflectionMemory("context", summary_interval=10)
        memory.update_from_trial(
            knobs={"cache": 10, "mode": "safe"},
            metrics={"latency": 100, "throughput": 50},
            score=50,
            trial_id="baseline",
        )
        delta = memory.update_from_trial(
            knobs={"cache": 15, "mode": "safe"},
            metrics={"latency": 90, "throughput": 55},
            score=55,
            trial_id="candidate",
        )

        self.assertEqual(delta["vs_best_turn"], 1)
        self.assertEqual(
            delta["knob_delta"]["cache"],
            {"from": 10, "to": 15, "change": 5.0},
        )
        self.assertNotIn("mode", delta["knob_delta"])
        self.assertEqual(
            delta["metric_highlights"]["latency"]["change"], -10.0
        )
        self.assertEqual(memory.pinned_trials["best"][0]["trial_id"], "candidate")

    def test_pinned_categories_and_ring_buffers_are_bounded(self):
        memory = ReflectionMemory(
            "context",
            summary_interval=100,
            recent_delta_limit=2,
            lesson_limit=2,
            pinned_limit=2,
        )
        for number in range(5):
            memory.update_from_trial(
                knobs={"cache": number},
                metrics={"throughput": number},
                score=number,
                trial_id="ok-%d" % number,
            )
        for number in range(3):
            memory.update_from_trial(
                knobs={"cache": -number},
                score=-number,
                success=False,
                trial_id="bad-%d" % number,
            )

        self.assertEqual(len(memory.recent_deltas), 2)
        self.assertLessEqual(len(memory.lessons), 2)
        self.assertEqual(len(memory.pinned_trials["best"]), 2)
        self.assertEqual(len(memory.pinned_trials["failure"]), 2)
        self.assertEqual(len(memory.pinned_trials["diverse"]), 2)
        self.assertEqual(memory.pinned_trials["best"][0]["score"], 4)
        self.assertEqual(
            [item["trial_id"] for item in memory.pinned_trials["failure"]],
            ["bad-1", "bad-2"],
        )

    def test_summary_due_by_interval_and_refreshes_automatically(self):
        memory = ReflectionMemory(
            "context", summary_interval=2, summary_char_budget=10000
        )
        memory.update_from_trial(knobs={"a": 1}, score=1)
        self.assertFalse(memory.should_summarize())
        self.assertEqual(memory.rolling_summary, "")

        memory.update_from_trial(knobs={"a": 2}, score=2)

        self.assertIn("turn=2", memory.rolling_summary)
        self.assertIn("best=t2 score=2", memory.rolling_summary)
        self.assertFalse(memory.should_summarize())

    def test_summary_due_by_character_budget(self):
        memory = ReflectionMemory(
            "context", summary_interval=100, summary_char_budget=80
        )
        self.assertTrue(memory.should_summarize(additional_chars=80))

        memory.update_from_trial(
            knobs={"a_long_knob_name": "a sufficiently long value"},
            metrics={"throughput": 123},
            score=123,
        )

        self.assertNotEqual(memory.rolling_summary, "")
        self.assertFalse(memory.should_summarize())

    def test_token_accounting_accumulates_and_survives_over_budget(self):
        memory = ReflectionMemory("context", token_budget=10)
        usage = memory.record_token_usage(3, 2)
        self.assertEqual(usage, {"prompt": 3, "completion": 2, "total": 5})

        memory.record_token_usage(4, 3, total_tokens=8)

        self.assertEqual(memory.token_usage["prompt"], 7)
        self.assertEqual(memory.token_usage["completion"], 5)
        self.assertEqual(memory.token_usage["total"], 13)
        self.assertEqual(memory.tokens_remaining, 0)
        with self.assertRaises(ValueError):
            memory.record_token_usage(-1)

    def test_round_trip_and_compact_prompt_context(self):
        memory = ReflectionMemory(
            {"workload": "tpcc"},
            token_budget=100,
            summary_interval=10,
        )
        memory.update_from_trial(
            {
                "trial_id": "first",
                "knobs": {"buffer": 64},
                "metrics": {"throughput": 42},
                "score": 42,
            }
        )
        memory.record_token_usage(7, 3)

        restored = ReflectionMemory.from_dict(
            json.loads(json.dumps(memory.to_dict()))
        )
        context = restored.compact_prompt_context(max_chars=2000)
        parsed = json.loads(context)

        self.assertEqual(restored.to_dict(), memory.to_dict())
        self.assertEqual(parsed["turn"], 1)
        self.assertEqual(parsed["tokens"]["used"], 10)
        self.assertLessEqual(len(context), 2000)
        self.assertLessEqual(len(restored.compact_prompt_context(120)), 120)

    def test_minimize_objective_tracks_lowest_score(self):
        memory = ReflectionMemory(
            "latency", objective="minimize", summary_interval=10
        )
        memory.update_from_trial(knobs={"a": 1}, score=20)
        memory.update_from_trial(knobs={"a": 2}, score=10)
        memory.update_from_trial(knobs={"a": 3}, score=15)

        self.assertEqual(memory.pinned_trials["best"][0]["score"], 10)
        self.assertIn("did not beat", memory.lessons[-1])


if __name__ == "__main__":
    unittest.main()
