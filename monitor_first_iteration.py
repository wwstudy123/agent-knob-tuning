#!/usr/bin/env python3
"""Monitor experiment and analyze results after first iteration completes."""

import sys
import time
import json
from pathlib import Path
from datetime import datetime

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from pie_analyzer import analyze_record_dir as analyze_pie
from hcbr_analyzer import analyze_record_dir as analyze_hcbr


def count_completed_iterations(record_dir: Path) -> int:
    """Count number of completed iterations based on turn files."""
    turn_files = list(record_dir.glob("turn_*"))
    return len(turn_files)


def check_first_iteration_complete(record_dir: Path) -> bool:
    """Check if first iteration has completed."""
    return count_completed_iterations(record_dir) >= 1


def wait_for_first_iteration(record_dir: Path, check_interval: int = 30):
    """Wait for first iteration to complete."""
    print(f"🔍 Monitoring {record_dir}...")
    print(f"   Checking every {check_interval} seconds")
    print()

    iteration_count = 0
    while True:
        current_iterations = count_completed_iterations(record_dir)

        if current_iterations > iteration_count:
            iteration_count = current_iterations
            print(f"✅ Iteration {iteration_count} completed at {datetime.now().strftime('%H:%M:%S')}")

            if iteration_count >= 1:
                print()
                print("=" * 70)
                print("First iteration complete! Analyzing results...")
                print("=" * 70)
                return True

        # Check if experiment is still running
        log_file = Path("/tmp/db_client.log")
        if log_file.exists():
            with open(log_file, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    print(f"\r⏳ Progress: {last_line[:60]}...", end="", flush=True)

        time.sleep(check_interval)


def analyze_results(record_dir: Path):
    """Analyze results from completed iterations."""
    print()
    print("=" * 70)
    print(f"📊 Analysis Results for {record_dir}")
    print("=" * 70)
    print()

    # Analyze PIE
    try:
        pie_result = analyze_pie(record_dir, metric_type='throughput')

        if 'error' not in pie_result:
            print("📈 Performance Metrics (PIE)")
            print("-" * 70)
            print(f"  P_0 (baseline):            {pie_result.get('p_0', 'N/A'):.6f}")
            print(f"  P_best (optimal):          {pie_result.get('p_best', 'N/A'):.6f}")
            print(f"  T_opt (iterations):        {pie_result.get('t_opt', 'N/A')}")
            print(f"  PIE:                       {pie_result.get('pie', 'N/A'):.6f}")
            print(f"  Improvement:               {pie_result.get('improvement_percent', 'N/A'):.2f}%")
            print()
        else:
            print(f"❌ PIE Analysis Error: {pie_result['error']}")
            print()
    except Exception as e:
        print(f"❌ PIE Analysis Failed: {e}")
        print()

    # Analyze HCBR
    try:
        hcbr_result = analyze_hcbr(record_dir)

        if 'error' not in hcbr_result:
            print("🛡️  Safety Metrics (HCBR)")
            print("-" * 70)
            print(f"  Total iterations:          {hcbr_result.get('total_iterations', 'N/A')}")
            print(f"  Baseline mean:             {hcbr_result.get('baseline_mean', 'N/A'):.6f}")
            print(f"  High-risk configs:         {hcbr_result.get('n_high_risk', 'N/A')}")
            print(f"  Blocked configs:           {hcbr_result.get('n_blocked', 'N/A')}")
            print(f"  HCBR:                      {hcbr_result.get('hcbr', 'N/A'):.2f}%")

            # Show high-risk details
            high_risk_configs = hcbr_result.get('high_risk_configs', [])
            if high_risk_configs:
                print(f"\n  High-risk configurations:")
                for config in high_risk_configs[:3]:
                    print(f"    - Iteration {config['iteration']}: "
                          f"throughput={config['throughput']:.6f}, "
                          f"reasons={', '.join(config['risk_reasons'])}")
            print()
        else:
            print(f"❌ HCBR Analysis Error: {hcbr_result['error']}")
            print()
    except Exception as e:
        print(f"❌ HCBR Analysis Failed: {e}")
        print()

    # Show decision log summary
    decision_log = record_dir / "decision_log.jsonl"
    if decision_log.exists():
        print("📋 Decision Log Summary")
        print("-" * 70)
        with open(decision_log, 'r') as f:
            decisions = [json.loads(line) for line in f if line.strip()]

        decision_counts = {}
        for d in decisions:
            name = d.get('decision', {}).get('name', 'unknown')
            decision_counts[name] = decision_counts.get(name, 0) + 1

        for name, count in decision_counts.items():
            print(f"  {name}: {count}")
        print()

    print("=" * 70)
    print("✅ Analysis complete!")
    print("=" * 70)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Monitor experiment and analyze after first iteration')
    parser.add_argument('--record-dir', type=Path, default=Path("configuration recommender/record-p1-verify"),
                       help='Record directory to monitor')
    parser.add_argument('--check-interval', type=int, default=30,
                       help='Check interval in seconds (default: 30)')
    parser.add_argument('--analyze-now', action='store_true',
                       help='Analyze existing results without waiting')

    args = parser.parse_args()

    record_dir = args.record_dir

    if not record_dir.exists():
        print(f"❌ Record directory {record_dir} does not exist")
        return 1

    if args.analyze_now:
        print("🔍 Analyzing existing results...")
        analyze_results(record_dir)
        return 0

    # Wait for first iteration
    if check_first_iteration_complete(record_dir):
        print("✅ First iteration already complete!")
        analyze_results(record_dir)
    else:
        print("⏳ Waiting for first iteration to complete...")
        wait_for_first_iteration(record_dir, args.check_interval)
        analyze_results(record_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
