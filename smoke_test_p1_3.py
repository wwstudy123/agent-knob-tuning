#!/usr/bin/env python3
"""Quick smoke test to verify P1.3 changes don't break imports."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'configuration recommender'))

print("="*60)
print("P1.3 Smoke Test")
print("="*60)

try:
    print("\n1. Importing SearchSpace...")
    from search_space import SearchSpace
    print("   ✅ SearchSpace imported")

    print("\n2. Importing DecisionController...")
    from decision_controller import DecisionController, TrialResult, ControllerAction
    print("   ✅ DecisionController imported")

    print("\n3. Testing SearchSpace functionality...")
    ss = SearchSpace({"knob1": {"type": "integer", "min_value": 1, "max_value": 100}})
    report = ss.shrink(center={"knob1": 50}, ratio=0.5)
    print(f"   ✅ SearchSpace shrink: {report}")

    print("\n4. Testing DecisionController with params...")
    dc = DecisionController(shrink_ratio=0.5, expand_ratio=0.25)
    print(f"   ✅ DecisionController created with shrink_ratio={dc.shrink_ratio}")

    print("\n5. Testing ControllerAction params...")
    action = ControllerAction(
        name="shrink_ranges",
        reason="test",
        rule="test",
        state=dc.state,
        params={"ratio": 0.5}
    )
    print(f"   ✅ ControllerAction with params: {action.params}")

    print("\n6. Testing serialization...")
    state = ss.to_dict()
    restored = SearchSpace.from_dict(state)
    print(f"   ✅ Serialization round-trip successful")

    print("\n" + "="*60)
    print("✅ All smoke tests passed!")
    print("="*60)
    print("\nReady to run full experiment.")

except Exception as e:
    print(f"\n❌ Smoke test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
