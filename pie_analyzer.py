#!/usr/bin/env python3
"""PIE (Performance Improvement Efficiency) analyzer.

PIE = (P_best - P_0) / T_opt

Where:
- P_0: baseline performance (default configuration)
- P_best: best performance achieved during tuning
- T_opt: number of iterations to reach P_best
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional


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


def extract_performance(history: List[Dict], metric_type: str = 'throughput') -> List[float]:
    """Extract performance values from history."""
    if metric_type == 'throughput':
        return [h.get('throughput', 0) for h in history]
    elif metric_type == 'latency':
        # For latency, lower is better, so we use 1/throughput as proxy
        return [1.0 / h.get('throughput', 1) if h.get('throughput', 0) > 0 else float('inf')
                for h in history]
    else:
        raise ValueError(f"Unknown metric type: {metric_type}")


def find_baseline_performance(history: List[Dict]) -> Optional[float]:
    """Find baseline performance (first iteration or default config)."""
    if not history:
        return None

    # Use first iteration as baseline
    first = history[0]
    return first.get('throughput', 0)


def find_best_performance(history: List[Dict]) -> Tuple[Optional[float], int]:
    """Find best performance and iteration number."""
    if not history:
        return None, -1

    best_throughput = 0
    best_iteration = -1

    for i, h in enumerate(history):
        throughput = h.get('throughput', 0)
        if throughput > best_throughput:
            best_throughput = throughput
            best_iteration = i

    return best_throughput, best_iteration


def calculate_pie(p_best: float, p_0: float, t_opt: int) -> float:
    """Calculate PIE (Performance Improvement Efficiency)."""
    if t_opt <= 0:
        return 0.0
    return (p_best - p_0) / t_opt


def analyze_record_dir(record_dir: Path, metric_type: str = 'throughput') -> Dict:
    """Analyze a single record directory."""
    history = load_benchmark_history(record_dir)

    if not history:
        return {
            'error': 'No benchmark history found',
            'record_dir': str(record_dir)
        }

    # Extract performance values
    performances = extract_performance(history, metric_type)

    # Find baseline (P_0)
    p_0 = find_baseline_performance(history)

    # Find best performance (P_best) and iteration (T_opt)
    p_best, t_opt = find_best_performance(history)

    # Calculate PIE
    pie = calculate_pie(p_best, p_0, t_opt + 1) if p_best and p_0 else 0.0

    return {
        'record_dir': str(record_dir),
        'total_iterations': len(history),
        'p_0': p_0,
        'p_best': p_best,
        't_opt': t_opt + 1,  # 1-indexed
        'pie': pie,
        'improvement_percent': ((p_best - p_0) / p_0 * 100) if p_0 > 0 else 0.0,
        'history': history
    }


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Analyze PIE (Performance Improvement Efficiency)')
    parser.add_argument('record_dirs', nargs='+', type=Path, help='Record directories to analyze')
    parser.add_argument('--metric', choices=['throughput', 'latency'], default='throughput',
                       help='Performance metric type (default: throughput)')
    parser.add_argument('--output', type=Path, help='Output JSON file')

    args = parser.parse_args()

    results = []
    for record_dir in args.record_dirs:
        if not record_dir.exists():
            print(f"Warning: {record_dir} does not exist, skipping")
            continue

        result = analyze_record_dir(record_dir, args.metric)
        results.append(result)

    # Print summary
    print("="*60)
    print("PIE Analysis Results")
    print("="*60)
    print()

    for result in results:
        if 'error' in result:
            print(f"❌ {result['record_dir']}: {result['error']}")
            continue

        print(f"✅ {result['record_dir']}")
        print(f"   P_0 (baseline):    {result['p_0']:.6f}")
        print(f"   P_best:            {result['p_best']:.6f}")
        print(f"   T_opt (iteration): {result['t_opt']}")
        print(f"   PIE:               {result['pie']:.6f}")
        print(f"   Improvement:       {result['improvement_percent']:.2f}%")
        print()

    # Save to JSON if requested
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {args.output}")

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
