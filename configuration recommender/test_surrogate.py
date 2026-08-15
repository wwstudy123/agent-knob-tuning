import json
import math
import os
import tempfile
import unittest

import surrogate


CANDIDATE = {
    "buffer_size": {"min": 0, "max": 999, "type": "integer"},
    "flush_mode": {"type": "enum", "enum_values": ["ON", "OFF"]},
    "worker_count": {"min": 1, "max": 64, "type": "integer"},
}

PRUNED = {
    "knob1": {"min_value": 9, "max_value": 999, "type": "integer"},
    # Deliberately omit enum_values: they must be recovered from CANDIDATE.
    "knob2": {"min_value": 0, "max_value": 1, "type": "enum"},
}


class EncoderTests(unittest.TestCase):
    def setUp(self):
        self.encoder = surrogate.CanonicalConfigEncoder(CANDIDATE, PRUNED)

    def test_accepts_real_and_anonymized_names(self):
        real = self.encoder.encode({"buffer_size": 99, "flush_mode": "OFF"})
        anonymous = self.encoder.encode({"knob1": 99, "knob2": 1})
        self.assertEqual(real, anonymous)
        self.assertAlmostEqual(real[0], 0.5)
        self.assertEqual(real[1:], [0.0, 1.0])
        self.assertEqual(
            self.encoder.canonicalize({"buffer_size": 99, "flush_mode": "OFF"}),
            {"knob1": 99, "knob2": "OFF"},
        )

    def test_pruned_range_overrides_candidate_and_values_are_clamped(self):
        self.assertEqual(self.encoder.encode({"knob1": -100})[0], 0.0)
        self.assertEqual(self.encoder.encode({"knob1": 100000})[0], 1.0)
        expected = (math.log1p(99) - math.log1p(9)) / (
            math.log1p(999) - math.log1p(9)
        )
        self.assertAlmostEqual(self.encoder.encode({"knob1": 99})[0], expected)

    def test_knob_form_wins_name_collision(self):
        canonical = self.encoder.canonicalize(
            {"knob1": 100, "buffer_size": 200, "unknown": 1}
        )
        self.assertEqual(canonical, {"knob1": 100})

    def test_diagnostics_are_json_serializable(self):
        payload = self.encoder.diagnostics()
        self.assertEqual(payload["feature_names"], ["knob1", "knob2=ON", "knob2=OFF"])
        json.dumps(payload)


class HistoryAndFallbackTests(unittest.TestCase):
    def setUp(self):
        self.encoder = surrogate.CanonicalConfigEncoder(CANDIDATE, PRUNED)

    def test_history_jsonl_loader_normalizes_and_skips_bad_lines(self):
        lines = [
            {"config": {"buffer_size": 10, "flush_mode": "ON"}, "throughput": 20},
            {"knobs": {"knob1": 20, "knob2": "OFF"}, "failed": True},
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        ) as handle:
            path = handle.name
            # Existing benchmark history appends pretty-printed objects, while
            # new producers may emit conventional one-object-per-line JSONL.
            handle.write(json.dumps(lines[0], indent=2) + "\n")
            handle.write("not json\n")
            handle.write(json.dumps(lines[1]) + "\n")
        try:
            loaded = surrogate.load_history_jsonl(path, self.encoder)
            self.assertEqual(len(loaded), 2)
            self.assertTrue(loaded[0]["success"])
            self.assertFalse(loaded[1]["success"])
            self.assertEqual(loaded[0]["config"]["knob1"], 10)
            with self.assertRaises(ValueError):
                surrogate.load_history_jsonl(path, self.encoder, strict=True)
        finally:
            os.unlink(path)

    def test_min_sample_fallback_prediction_and_ranking(self):
        model = surrogate.SurrogateModel(self.encoder, min_samples=100)
        model.fit(
            [
                {"config": {"knob1": 10, "knob2": "ON"}, "throughput": 100},
                {"config": {"knob1": 20, "knob2": "OFF"}, "throughput": 200},
                {
                    "config": {"knob1": 30, "knob2": "ON"},
                    "throughput": 0,
                    "success": False,
                },
            ]
        )
        prediction = model.predict({"buffer_size": 50, "flush_mode": "ON"})
        self.assertAlmostEqual(prediction["mean"], 150.0)
        self.assertAlmostEqual(prediction["std"], 50.0)
        self.assertAlmostEqual(prediction["success_probability"], 2.0 / 3.0)
        ranked = model.rank_candidates(
            [
                {"buffer_size": 40, "flush_mode": "ON"},
                {"knob1": 80, "knob2": "OFF"},
            ],
            top_k=1,
            strategy="ei",
        )
        self.assertEqual(len(ranked), 1)
        self.assertTrue(ranked[0]["exploration"])
        json.dumps(ranked)
        diagnostics = model.diagnostics()
        self.assertEqual(diagnostics["mode"], "fallback")
        json.dumps(diagnostics)


@unittest.skipUnless(surrogate.SKLEARN_AVAILABLE, "scikit-learn is not installed")
class SklearnModelTests(unittest.TestCase):
    def test_two_stage_models_fit_and_predict(self):
        encoder = surrogate.CanonicalConfigEncoder(CANDIDATE, PRUNED)
        history = []
        for index in range(12):
            success = index not in {2, 7, 10}
            history.append(
                {
                    "config": {
                        "knob1": 10 + index * 70,
                        "knob2": "ON" if index % 2 else "OFF",
                    },
                    "throughput": (100 + index * 8) if success else 0,
                    "success": success,
                }
            )
        model = surrogate.SurrogateModel(
            encoder, min_samples=8, min_successes=3, random_state=4, n_estimators=20
        ).fit(history)
        diagnostics = model.diagnostics()
        self.assertTrue(diagnostics["classifier_fitted"])
        self.assertTrue(diagnostics["regressor_fitted"])
        prediction = model.predict({"knob1": 300, "knob2": "ON"})
        self.assertGreaterEqual(prediction["mean"], 0)
        self.assertGreaterEqual(prediction["std"], 0)
        self.assertGreaterEqual(prediction["success_probability"], 0)
        self.assertLessEqual(prediction["success_probability"], 1)


if __name__ == "__main__":
    unittest.main()
