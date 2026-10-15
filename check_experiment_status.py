#!/usr/bin/env python3
"""Check P1.3 verification experiment status."""

import json
import os
from pathlib import Path

RECORD_DIR = Path("configuration recommender/record-p1-verify")

print("="*60)
print("P1.3 Verification Experiment Status")
print("="*60)
print()

# Check checkpoint
checkpoint_path = RECORD_DIR / "checkpoint.json"
if checkpoint_path.exists():
    with open(checkpoint_path, 'r') as f:
        ckpt = json.load(f)

    print("✅ Checkpoint found")
    print(f"   Status: {ckpt.get('status', 'unknown')}")
    print(f"   Iteration: {ckpt.get('iteration', 'N/A')}")
    print(f"   Result index: {ckpt.get('result_index', 'N/A')}")
    print()

    # Check search_space
    if 'search_space' in ckpt:
        print("✅ search_space in checkpoint")
        search_space = ckpt['search_space']
        if 'current' in search_space:
            print(f"   Knobs tracked: {len(search_space['current'])}")
            # Show first 3 knobs
            for i, (key, meta) in enumerate(list(search_space['current'].items())[:3]):
                print(f"   {key}: [{meta.get('min_value')}, {meta.get('max_value')}]")
    else:
        print("❌ search_space NOT in checkpoint")
    print()
else:
    print("⏳ Checkpoint not yet created")
    print()

# Check decision log
decision_log_path = RECORD_DIR / "decision_log.jsonl"
if decision_log_path.exists():
    with open(decision_log_path, 'r') as f:
        decisions = [json.loads(line) for line in f if line.strip()]

    print(f"✅ Decision log: {len(decisions)} decisions")
    if decisions:
        decision_counts = {}
        for d in decisions:
            name = d.get('name', 'unknown')
            decision_counts[name] = decision_counts.get(name, 0) + 1

        print("   Decision breakdown:")
        for name, count in sorted(decision_counts.items()):
            print(f"     - {name}: {count}")

        # Show last decision with params
        last = decisions[-1]
        print(f"\n   Last decision:")
        print(f"     Name: {last.get('name')}")
        print(f"     Reason: {last.get('reason', 'N/A')[:60]}...")
        if 'params' in last and last['params']:
            print(f"     Params: {last['params']}")
else:
    print("⏳ Decision log not yet created")
print()

# Check turn files
turn_files = sorted(RECORD_DIR.glob("turn_*"))
print(f"✅ Turn files: {len(turn_files)}")
if turn_files:
    for tf in turn_files[:5]:
        with open(tf, 'r') as f:
            content = f.read().strip()
            configs = [json.loads(line) for line in content.split('\n') if line.strip()]
        print(f"   {tf.name}: {len(configs)} config(s)")
print()

# Check token usage
token_log_path = RECORD_DIR / "token_usage.jsonl"
if token_log_path.exists():
    with open(token_log_path, 'r') as f:
        tokens = [json.loads(line) for line in f if line.strip()]

    if tokens:
        total_tokens = tokens[-1].get('cumulative_total_tokens', 0)
        print(f"✅ Token usage: {total_tokens:,} tokens")
else:
    print("⏳ Token log not yet created")
print()

# Summary
print("="*60)
if checkpoint_path.exists():
    status = ckpt.get('status', 'unknown')
    if status == 'completed':
        print("✅ EXPERIMENT COMPLETED")
        print("\nNext step: Run verification")
        print("  python verify_p1_3.py")
    else:
        print(f"⏳ Experiment in progress ({status})")
        print(f"   Please wait for completion...")
else:
    print("⏳ Experiment starting up...")
print("="*60)
