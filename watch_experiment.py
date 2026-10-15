#!/usr/bin/env python3
"""Monitor experiment until completion."""

import time
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '.')

def check_status():
    """Check experiment status."""
    record_dir = Path("configuration recommender/record-p1-verify")
    checkpoint_path = record_dir / "checkpoint.json"

    if not checkpoint_path.exists():
        return "starting", None

    import json
    with open(checkpoint_path, 'r') as f:
        ckpt = json.load(f)

    status = ckpt.get('status', 'unknown')
    iteration = ckpt.get('iteration', 0)
    has_search_space = 'search_space' in ckpt

    return status, {
        'iteration': iteration,
        'has_search_space': has_search_space,
        'checkpoint_path': checkpoint_path
    }

def main():
    """Monitor until completion."""
    print("="*60)
    print("P1.3 Experiment Monitor")
    print("="*60)
    print()
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    last_status = None
    check_count = 0

    while True:
        status, info = check_status()

        # Print status if changed or every 10 checks
        if status != last_status or check_count % 10 == 0:
            timestamp = datetime.now().strftime('%H:%M:%S')

            if status == "starting":
                print(f"[{timestamp}] ⏳  Experiment starting up...")
            elif status == "running":
                iteration = info['iteration'] if info else '?'
                has_ss = "✅" if (info and info['has_search_space']) else "❌"
                print(f"[{timestamp}] ⏳  Running iteration {iteration} | search_space: {has_ss}")
            elif status == "completed":
                print(f"[{timestamp}] ✅  EXPERIMENT COMPLETED!")
                print()
                print("="*60)
                print("Next step: Run verification")
                print("  python verify_p1_3.py")
                print("="*60)
                break
            else:
                print(f"[{timestamp}] ❓  Unknown status: {status}")

            last_status = status

        # Check every 15 seconds
        time.sleep(15)
        check_count += 1

        # Safety timeout (2 hours)
        if check_count > 480:  # 480 * 15s = 2 hours
            print()
            print("⚠️  Monitor timeout after 2 hours")
            print("   Please check manually:")
            print("   python check_experiment_status.py")
            break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nMonitor stopped by user")
        sys.exit(0)
