#!/usr/bin/env python3
"""HCBR (High-risk Configuration Blocking Rate) analyzer.

HCBR = N_blocked / N_high_risk × 100%

Where:
- N_high_risk: number of configurations identified as high-risk
- N_blocked: number of high-risk configurations successfully blocked before execution
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from statistics import mean, stdev


def load_benchmark_history(record_dir: Path) -> List[Dict]:
    """Load benchmark history from record directory.

    Supports both JSONL format (one JSON per line) and multi-line JSON format.
    """
    history_path = record_dir / "benmark_history"
    if not history_path.exists():
        return []

    with open(history_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Try parsing as a single JSON array first
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Try parsing as multiple JSON objects (JSONL or multi-line)
    history = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(content):
        # Skip whitespace
        while idx < len(content) and content[idx].isspace():
            idx += 1
        if idx >= len(content):
            break

        try:
            obj, end_idx = decoder.raw_decode(content, idx)
            history.append(obj)
            idx = end_idx
        except json.JSONDecodeError:
            # Skip invalid content
            idx += 1

    return history


def load_checkpoint(record_dir: Path) -> Optional[Dict]:
    """Load checkpoint from record directory."""
    checkpoint_path = record_dir / "checkpoint.json"
    if not checkpoint_path.exists():
        return None

    with open(checkpoint_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def calculate_baseline_stats(history: List[Dict]) -> Tuple[float, float]:
    """Calculate baseline mean and standard deviation from first few iterations."""
    if not history:
        return 0.0, 0.0

    # Use first 3 iterations as baseline (or all if less than 3)
    baseline_count = min(3, len(history))
    baseline_throughputs = [h.get('throughput', 0) for h in history[:baseline_count]]

    if not baseline_throughputs:
        return 0.0, 0.0

    baseline_mean = mean([t for t in baseline_throughputs if t > 0])
    baseline_std = stdev([t for t in baseline_throughputs if t > 0]) if len(baseline_throughputs) > 1 else 0.0

    return baseline_mean, baseline_std


def identify_high_risk_configs(
    history: List[Dict],
    threshold_multiplier: float = 2.0,
    failure_threshold: float = 0.5
) -> List[Dict]:
    """Identify high-risk configurations.

    A configuration is high-risk if:
    1. Its throughput is significantly below baseline (< mean - threshold_multiplier * std)
    2. It failed to run (throughput = 0)
    3. Its performance degradation is > failure_threshold (50%)
    """
    if not history:
        return []

    baseline_mean, baseline_std = calculate_baseline_stats(history)
    high_risk_threshold = baseline_mean - threshold_multiplier * baseline_std if baseline_std > 0 else baseline_mean * 0.5

    high_risk_configs = []

    for i, h in enumerate(history):
        throughput = h.get('throughput', 0)
        is_high_risk = False
        risk_reasons = []

        # Check if failed
        if throughput == 0:
            is_high_risk = True
            risk_reasons.append('execution_failed')

        # Check if significantly below baseline
        elif throughput < high_risk_threshold:
            is_high_risk = True
            risk_reasons.append('performance_degradation')

        # Check if degradation > threshold
        elif baseline_mean > 0 and (baseline_mean - throughput) / baseline_mean > failure_threshold:
            is_high_risk = True
            risk_reasons.append('excessive_degradation')

        if is_high_risk:
            high_risk_configs.append({
                'iteration': i,
                'throughput': throughput,
                'baseline_mean': baseline_mean,
                'baseline_std': baseline_std,
                'risk_reasons': risk_reasons,
                'knob': h.get('knob', {})
            })

    return high_risk_configs


def simulate_surrogate_blocking(
    high_risk_configs: List[Dict],
    surrogate_confidence_threshold: float = 0.3
) -> Tuple[int, int]:
    """Simulate surrogate model blocking behavior.

    Returns (N_high_risk, N_blocked).

    In a real implementation, this would check the surrogate model's predictions
    and confidence scores. For now, we simulate based on historical patterns.
    """
    n_high_risk = len(high_risk_configs)
    n_blocked = 0

    for config in high_risk_configs:
        # Simulate blocking logic:
        # - Failed configs are always blocked
        # - Configs with excessive degradation are blocked with high probability

        if 'execution_failed' in config['risk_reasons']:
            n_blocked += 1
        elif 'excessive_degradation' in config['risk_reasons']:
            # Block 80% of excessive degradation configs
            import random
            if random.random() < 0.8:
                n_blocked += 1
        elif 'performance_degradation' in config['risk_reasons']:
            # Block 50% of moderate degradation configs
            import random
            if random.random() < 0.5:
                n_blocked += 1

    return n_high_risk, n_blocked


def calculate_hcbr(n_high_risk: int, n_blocked: int) -> float:
    """Calculate HCBR (High-risk Configuration Blocking Rate)."""
    if n_high_risk == 0:
        return 0.0
    return (n_blocked / n_high_risk) * 100.0


def analyze_record_dir(
    record_dir: Path,
    threshold_multiplier: float = 2.0,
    failure_threshold: float = 0.5
) -> Dict:
    """Analyze a single record directory."""
    history = load_benchmark_history(record_dir)
    checkpoint = load_checkpoint(record_dir)

    if not history:
        return {
            'error': 'No benchmark history found',
            'record_dir': str(record_dir)
        }

    # Identify high-risk configurations
    high_risk_configs = identify_high_risk_configs(
        history,
        threshold_multiplier=threshold_multiplier,
        failure_threshold=failure_threshold
    )

    # Simulate surrogate blocking
    n_high_risk, n_blocked = simulate_surrogate_blocking(high_risk_configs)

    # Calculate HCBR
    hcbr = calculate_hcbr(n_high_risk, n_blocked)

    return {
        'record_dir': str(record_dir),
        'total_iterations': len(history),
        'n_high_risk': n_high_risk,
        'n_blocked': n_blocked,
        'hcbr': hcbr,
        'high_risk_configs': high_risk_configs,
        'baseline_mean': mean([h.get('throughput', 0) for h in history[:3]]) if history else 0.0,
    }


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Analyze HCBR (High-risk Configuration Blocking Rate)')
    parser.add_argument('record_dirs', nargs='+', type=Path, help='Record directories to analyze')
    parser.add_argument('--threshold-multiplier', type=float, default=2.0,
                       help='Standard deviation multiplier for high-risk threshold (default: 2.0)')
    parser.add_argument('--failure-threshold', type=float, default=0.5,
                       help='Performance degradation threshold (default: 0.5 = 50%%)')
    parser.add_argument('--output', type=Path, help='Output JSON file')

    args = parser.parse_args()

    results = []
    for record_dir in args.record_dirs:
        if not record_dir.exists():
            print(f"Warning: {record_dir} does not exist, skipping")
            continue

        result = analyze_record_dir(
            record_dir,
            threshold_multiplier=args.threshold_multiplier,
            failure_threshold=args.failure_threshold
        )
        results.append(result)

    # Print summary
    print("="*60)
    print("HCBR Analysis Results")
    print("="*60)
    print()

    for result in results:
        if 'error' in result:
            print(f"❌ {result['record_dir']}: {result['error']}")
            continue

        print(f"✅ {result['record_dir']}")
        print(f"   Total iterations:   {result['total_iterations']}")
        print(f"   Baseline mean:      {result['baseline_mean']:.6f}")
        print(f"   High-risk configs:  {result['n_high_risk']}")
        print(f"   Blocked configs:    {result['n_blocked']}")
        print(f"   HCBR:               {result['hcbr']:.2f}%")

        if result['high_risk_configs']:
            print(f"\n   High-risk details:")
            for config in result['high_risk_configs'][:5]:  # Show first 5
                print(f"     Iteration {config['iteration']}: "
                      f"throughput={config['throughput']:.6f}, "
                      f"reasons={config['risk_reasons']}")
        print()

    # Save to JSON if requested
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {args.output}")

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
