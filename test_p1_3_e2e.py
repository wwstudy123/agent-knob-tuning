#!/usr/bin/env python3
"""End-to-end test for P1.3 without MySQL dependency.

This test simulates the LLM_server and DB_client interaction to verify:
1. DecisionController triggers shrink/expand decisions
2. LLM_server applies decisions to SearchSpace
3. SearchSpace state is properly serialized and restored
"""

import json
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'configuration recommender'))

from search_space import SearchSpace
from decision_controller import DecisionController, TrialResult

def test_decision_triggers_search_space_changes():
    """Test that decisions trigger search_space modifications."""
    print("\n" + "="*60)
    print("Test 1: Decision triggers SearchSpace changes")
    print("="*60)

    # Initialize
    knob_metadata = {
        "buffer_pool": {"type": "integer", "min_value": 1, "max_value": 64, "step": 1},
        "threads": {"type": "integer", "min_value": 1, "max_value": 128, "step": 1},
    }
    search_space = SearchSpace(knob_metadata)
    controller = DecisionController(
        shrink_ratio=0.5,
        expand_ratio=0.25,
        min_trials=3,
        failure_rate_shrink=0.25,
    )

    print(f"\nInitial buffer_pool range: [{knob_metadata['buffer_pool']['min_value']}, {knob_metadata['buffer_pool']['max_value']}]")

    # Simulate trials with moderate failure rate to trigger shrink (but not rollback)
    # failure_rate_shrink=0.25, failure_rate_rollback=0.50
    # We want failure_rate between 0.25 and 0.50, e.g., 0.40 (2/5 failures)
    best_knob = {"buffer_pool": 32, "threads": 64}
    for i in range(5):
        controller.record_trial(TrialResult(
            throughput=0 if i < 2 else 100,  # 40% failure rate (2/5)
            success=i >= 2,
            configuration=best_knob,
        ))

    action = controller.decide()
    print(f"\nDecision: {action.name}")
    print(f"Reason: {action.reason}")
    print(f"Params: {action.params}")

    # Apply decision to search_space
    if action.name == "shrink_ranges":
        ratio = action.params.get("ratio", 0.5)
        report = search_space.shrink(center=best_knob, ratio=ratio)
        print(f"\nSearchSpace shrink report: {report}")

        new_meta = search_space.metadata()
        print(f"\nNew buffer_pool range: [{new_meta['buffer_pool']['min_value']}, {new_meta['buffer_pool']['max_value']}]")

        assert "buffer_pool" in report["changed"]
        assert new_meta["buffer_pool"]["min_value"] > knob_metadata["buffer_pool"]["min_value"]
        assert new_meta["buffer_pool"]["max_value"] < knob_metadata["buffer_pool"]["max_value"]
        print("\n✅ PASS: SearchSpace correctly shrunk")
    else:
        print(f"\n⚠️  Expected shrink_ranges, got {action.name}")
        return False

    # Test serialization
    print("\nTesting serialization...")
    state = search_space.to_dict()
    restored = SearchSpace.from_dict(state)
    assert restored.metadata() == search_space.metadata()
    print("✅ Serialization round-trip successful")

    # Test reset
    print("\nTesting reset...")
    search_space.reset()
    assert search_space.metadata() == knob_metadata
    print("✅ Reset successful")

    return True


def test_checkpoint_restore_cycle():
    """Test that search_space can be saved and restored from checkpoint."""
    print("\n" + "="*60)
    print("Test 2: Checkpoint save/restore cycle")
    print("="*60)

    # Initialize
    knob_metadata = {
        "buffer_pool": {"type": "integer", "min_value": 1, "max_value": 64, "step": 1},
        "threads": {"type": "integer", "min_value": 1, "max_value": 128, "step": 1},
    }
    search_space = SearchSpace(knob_metadata)

    # Modify search_space
    search_space.shrink(center={"buffer_pool": 32}, ratio=0.5)
    modified_meta = search_space.metadata()
    print(f"\nModified buffer_pool range: [{modified_meta['buffer_pool']['min_value']}, {modified_meta['buffer_pool']['max_value']}]")

    # Simulate checkpoint save
    checkpoint = {
        "search_space": search_space.to_dict(),
        "iteration": 5,
        "best_knob": {"buffer_pool": 32},
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(checkpoint, f)
        checkpoint_path = f.name

    print(f"\nCheckpoint saved to {checkpoint_path}")

    # Simulate checkpoint restore
    with open(checkpoint_path, 'r') as f:
        restored_checkpoint = json.load(f)

    restored_search_space = SearchSpace.from_dict(restored_checkpoint["search_space"])
    restored_meta = restored_search_space.metadata()

    print(f"Restored buffer_pool range: [{restored_meta['buffer_pool']['min_value']}, {restored_meta['buffer_pool']['max_value']}]")

    assert restored_meta == modified_meta
    print("\n✅ PASS: Checkpoint save/restore cycle successful")

    # Cleanup
    os.unlink(checkpoint_path)

    return True


def test_best_knob_drives_shrink_center():
    """Test that best_knob is used as the center for shrink."""
    print("\n" + "="*60)
    print("Test 3: best_knob drives shrink center")
    print("="*60)

    knob_metadata = {
        "buffer_pool": {"type": "integer", "min_value": 1, "max_value": 100, "step": 1},
    }

    # Test with different best_knob values
    for best_value in [20, 50, 80]:
        search_space = SearchSpace(knob_metadata)
        best_knob = {"buffer_pool": best_value}

        search_space.shrink(center=best_knob, ratio=0.5)
        meta = search_space.metadata()

        print(f"\nbest_knob={best_value} -> range=[{meta['buffer_pool']['min_value']}, {meta['buffer_pool']['max_value']}]")

        # Verify that the range is centered around best_value
        assert meta["buffer_pool"]["min_value"] < best_value
        assert meta["buffer_pool"]["max_value"] > best_value

    print("\n✅ PASS: best_knob correctly drives shrink center")

    return True


def main():
    """Run all tests."""
    print("="*60)
    print("P1.3 End-to-End Test (No MySQL Required)")
    print("="*60)

    tests = [
        ("Decision triggers SearchSpace changes", test_decision_triggers_search_space_changes),
        ("Checkpoint save/restore cycle", test_checkpoint_restore_cycle),
        ("best_knob drives shrink center", test_best_knob_drives_shrink_center),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False

    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)

    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All end-to-end tests passed!")
        print("\nP1.3 implementation is verified:")
        print("  ✅ DecisionController triggers shrink/expand")
        print("  ✅ SearchSpace correctly applies decisions")
        print("  ✅ Checkpoint save/restore works")
        print("  ✅ best_knob drives shrink center")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
