import json
import unittest

from decision_controller import (
    ACTIONS,
    ControllerAction,
    ControllerState,
    DecisionController,
    TrialResult,
)


def successful_trials(
    throughputs, confidence=0.5, configurations=None, token_cost=0
):
    if configurations is None:
        configurations = [{"knob": index} for index in range(len(throughputs))]
    return [
        TrialResult(
            throughput=throughput,
            configuration=configuration,
            surrogate_confidence=confidence,
            token_cost=token_cost,
        )
        for throughput, configuration in zip(throughputs, configurations)
    ]


class DecisionControllerStateTests(unittest.TestCase):
    def test_empty_history_is_safe_and_explainable(self):
        controller = DecisionController()

        state = controller.state
        action = controller.decide()

        self.assertEqual(0, state.trial_count)
        self.assertIsNone(state.best_throughput)
        self.assertEqual(0.0, state.failure_rate)
        self.assertEqual("continue_local", action.name)
        self.assertEqual("no_history", action.rule)

    def test_state_uses_recent_window_and_tracks_total_budgets(self):
        controller = DecisionController(
            window_size=3, benchmark_budget=5, token_budget=100
        )
        for trial in successful_trials(
            [50.0, 80.0, 100.0, 110.0],
            confidence=0.8,
            token_cost=20,
        ):
            controller.record_trial(trial)

        state = controller.state

        self.assertEqual(3, state.trial_count)
        self.assertEqual(110.0, state.best_throughput)
        self.assertAlmostEqual(0.375, state.improvement_rate)
        self.assertEqual(4, state.benchmark_used)
        self.assertEqual(1, state.benchmark_remaining)
        self.assertEqual(80, state.tokens_used)
        self.assertEqual(20, state.tokens_remaining)
        self.assertFalse(state.budget_exhausted)

    def test_failure_diversity_and_confidence_metrics(self):
        controller = DecisionController(window_size=4)
        controller.record_trial(
            TrialResult(
                throughput=100,
                configuration={"a": 1},
                surrogate_confidence=0.6,
            )
        )
        controller.record_trial(
            TrialResult(
                throughput=102,
                configuration={"a": 1},
                surrogate_confidence=0.8,
            )
        )
        controller.record_trial(
            TrialResult(
                throughput=101,
                configuration={"a": 2},
                surrogate_confidence=None,
            )
        )
        controller.record_trial(TrialResult(success=False))

        state = controller.state

        self.assertEqual(102.0, state.best_throughput)
        self.assertAlmostEqual(0.01, state.improvement_rate)
        self.assertAlmostEqual(0.25, state.failure_rate)
        self.assertAlmostEqual(2.0 / 3.0, state.diversity)
        self.assertAlmostEqual(0.7, state.surrogate_confidence)

    def test_sparse_failed_history_does_not_trigger_rollback_early(self):
        controller = DecisionController(min_trials=3)
        controller.record_trial(TrialResult(success=False))

        action = controller.decide()

        self.assertEqual("continue_local", action.name)
        self.assertEqual("sparse_history", action.rule)


class DecisionControllerPolicyTests(unittest.TestCase):
    def make_controller(self, trials, **settings):
        controller = DecisionController(**settings)
        for trial in trials:
            controller.record_trial(trial)
        return controller

    def test_every_declared_action_has_a_policy_path(self):
        scenarios = {}

        scenarios["continue_local"] = self.make_controller(
            successful_trials([100, 102, 102], confidence=0.5)
        )
        scenarios["expand_ranges"] = self.make_controller(
            successful_trials([100, 105, 110], confidence=0.8)
        )

        shrink_trials = successful_trials([100, 101, 102])
        shrink_trials.append(TrialResult(success=False))
        scenarios["shrink_ranges"] = self.make_controller(shrink_trials)

        scenarios["reselect_knobs"] = self.make_controller(
            successful_trials([100] * 6, confidence=0.8)
        )
        scenarios["increase_exploration"] = self.make_controller(
            successful_trials(
                [100, 101, 102],
                confidence=0.5,
                configurations=[{"a": 1}, {"a": 1}, {"a": 1}],
            )
        )

        rollback_trials = successful_trials([100, 101])
        rollback_trials.extend(
            [TrialResult(success=False), TrialResult(success=False)]
        )
        scenarios["rollback_safe"] = self.make_controller(rollback_trials)

        scenarios["reflect"] = self.make_controller(
            successful_trials([100] * 4, confidence=None)
        )
        scenarios["stop"] = self.make_controller(
            successful_trials([100, 101]),
            benchmark_budget=2,
        )

        observed = {
            expected: controller.decide().name
            for expected, controller in scenarios.items()
        }
        self.assertEqual(set(ACTIONS), set(observed))
        self.assertEqual({name: name for name in ACTIONS}, observed)

    def test_custom_thresholds_change_policy_deterministically(self):
        trials = successful_trials([100, 104, 108], confidence=0.65)
        default_action = self.make_controller(trials).decide()
        custom_action = self.make_controller(
            trials,
            improvement_expand=0.07,
            confidence_high=0.60,
        ).decide()

        self.assertEqual("continue_local", default_action.name)
        self.assertEqual("expand_ranges", custom_action.name)
        self.assertIn("0.080", custom_action.reason)

    def test_token_budget_stops_before_other_rules(self):
        controller = self.make_controller(
            successful_trials([100], confidence=0.9, token_cost=10),
            token_budget=10,
        )

        action = controller.decide()

        self.assertEqual("stop", action.name)
        self.assertEqual("budget_exhausted", action.rule)


class DecisionControllerSerializationTests(unittest.TestCase):
    def test_controller_round_trip_is_json_serializable_and_lossless(self):
        controller = DecisionController(
            window_size=5, benchmark_budget=20, token_budget=1_000
        )
        controller.record_trial(
            {
                "throughput": 123.4,
                "configuration": {"shared_buffers": "2GB"},
                "surrogate_confidence": 0.75,
                "token_cost": 40,
                "metadata": {"source": "test"},
            }
        )
        controller.decide()

        payload = json.loads(json.dumps(controller.to_dict()))
        restored = DecisionController.from_dict(payload)

        self.assertEqual(controller.to_dict(), restored.to_dict())
        self.assertEqual(controller.state, restored.state)
        self.assertEqual(1, len(restored.state_history))
        self.assertIsInstance(restored.trial_history[0], TrialResult)
        self.assertIsInstance(restored.state_history[0], ControllerState)
        self.assertIsInstance(restored.action_history[0], ControllerAction)
        self.assertIsInstance(restored.action_history[0].state, ControllerState)

    def test_invalid_checkpoint_version_is_rejected(self):
        with self.assertRaises(ValueError):
            DecisionController.from_dict({"version": 999})

    def test_trial_validation_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            TrialResult(throughput=None, success=True)
        with self.assertRaises(ValueError):
            TrialResult(throughput=1, surrogate_confidence=1.1)
        with self.assertRaises(ValueError):
            TrialResult(throughput=float("nan"))


if __name__ == "__main__":
    unittest.main()

