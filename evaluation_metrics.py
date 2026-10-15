#!/usr/bin/env python3
"""Unified evaluation metrics analyzer for ADMTune.

Combines PIE and HCBR analysis into a comprehensive evaluation report.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List
from datetime import datetime

from pie_analyzer import analyze_record_dir as analyze_pie
from hcbr_analyzer import analyze_record_dir as analyze_hcbr


def generate_evaluation_report(
    record_dirs: List[Path],
    output_path: Path = None
) -> Dict:
    """Generate comprehensive evaluation report."""

    report = {
        'timestamp': datetime.now().isoformat(),
        'record_dirs': [str(d) for d in record_dirs],
        'experiments': []
    }

    for record_dir in record_dirs:
        if not record_dir.exists():
            print(f"Warning: {record_dir} does not exist, skipping")
            continue

        print(f"\nAnalyzing {record_dir}...")

        # Analyze PIE
        pie_result = analyze_pie(record_dir, metric_type='throughput')

        # Analyze HCBR
        hcbr_result = analyze_hcbr(record_dir)

        experiment = {
            'record_dir': str(record_dir),
            'pie': pie_result,
            'hcbr': hcbr_result,
        }

        report['experiments'].append(experiment)

    # Save report if output path provided
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nReport saved to {output_path}")

    return report


def print_report(report: Dict):
    """Print evaluation report to console."""

    print("\n" + "="*70)
    print("ADMTune Evaluation Report")
    print("="*70)
    print(f"Generated at: {report['timestamp']}")
    print(f"Experiments analyzed: {len(report['experiments'])}")
    print("="*70)

    for exp in report['experiments']:
        print(f"\n{'='*70}")
        print(f"Experiment: {exp['record_dir']}")
        print(f"{'='*70}")

        # PIE metrics
        pie = exp.get('pie', {})
        if 'error' not in pie:
            print("\n📊 Performance Metrics (PIE)")
            print("-" * 70)
            print(f"  P_0 (baseline):            {pie.get('p_0', 'N/A')}")
            print(f"  P_best (optimal):          {pie.get('p_best', 'N/A')}")
            print(f"  T_opt (iterations):        {pie.get('t_opt', 'N/A')}")
            print(f"  PIE:                       {pie.get('pie', 'N/A')}")
            print(f"  Improvement:               {pie.get('improvement_percent', 'N/A'):.2f}%")
        else:
            print(f"\n❌ PIE Analysis Error: {pie['error']}")

        # HCBR metrics
        hcbr = exp.get('hcbr', {})
        if 'error' not in hcbr:
            print("\n🛡️  Safety Metrics (HCBR)")
            print("-" * 70)
            print(f"  Baseline mean:             {hcbr.get('baseline_mean', 'N/A'):.6f}")
            print(f"  High-risk configs:         {hcbr.get('n_high_risk', 'N/A')}")
            print(f"  Blocked configs:           {hcbr.get('n_blocked', 'N/A')}")
            print(f"  HCBR:                      {hcbr.get('hcbr', 'N/A'):.2f}%")

            # Show high-risk details
            high_risk_configs = hcbr.get('high_risk_configs', [])
            if high_risk_configs:
                print(f"\n  High-risk configurations:")
                for config in high_risk_configs[:3]:
                    print(f"    - Iteration {config['iteration']}: "
                          f"throughput={config['throughput']:.6f}, "
                          f"reasons={', '.join(config['risk_reasons'])}")
        else:
            print(f"\n❌ HCBR Analysis Error: {hcbr['error']}")

    print("\n" + "="*70)
    print("Summary")
    print("="*70)

    # Calculate averages
    pie_values = [exp['pie']['pie'] for exp in report['experiments'] if 'error' not in exp.get('pie', {})]
    hcbr_values = [exp['hcbr']['hcbr'] for exp in report['experiments'] if 'error' not in exp.get('hcbr', {})]

    if pie_values:
        avg_pie = sum(pie_values) / len(pie_values)
        print(f"\nAverage PIE:  {avg_pie:.6f}")
    if hcbr_values:
        avg_hcbr = sum(hcbr_values) / len(hcbr_values)
        print(f"Average HCBR: {avg_hcbr:.2f}%")

    print("\n" + "="*70)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Generate comprehensive ADMTune evaluation report')
    parser.add_argument('record_dirs', nargs='+', type=Path, help='Record directories to analyze')
    parser.add_argument('--output', type=Path, help='Output JSON file')

    args = parser.parse_args()

    # Generate report
    report = generate_evaluation_report(args.record_dirs, args.output)

    # Print report
    print_report(report)

    return 0 if report['experiments'] else 1


if __name__ == "__main__":
    sys.exit(main())
