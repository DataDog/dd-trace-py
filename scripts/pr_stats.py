#!/usr/bin/env python3
"""
PR stats for dd-trace-py over the past 6 months.
Shows: PRs opened, PRs merged, and month-over-month rate of change.
"""

import calendar
import json
import subprocess
import sys
from datetime import datetime, timezone


def run_gh(args):
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, check=True)
    return result.stdout


def count_opened(repo, year, month):
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day:02d}"
    output = run_gh([
        "api", "search/issues",
        "--method", "GET",
        "-f", f"q=repo:{repo} is:pr created:{start}..{end}",
        "-F", "per_page=1",
        "--jq", ".total_count",
    ])
    return int(output.strip())


def count_merged(repo, year, month):
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day:02d}"
    output = run_gh([
        "api", "search/issues",
        "--method", "GET",
        "-f", f"q=repo:{repo} is:pr is:merged merged:{start}..{end}",
        "-F", "per_page=1",
        "--jq", ".total_count",
    ])
    return int(output.strip())


def iter_months(months):
    today = datetime.now(timezone.utc)
    year, month = today.year, today.month
    result = []
    for _ in range(months):
        result.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(result))


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "DataDog/dd-trace-py"
    num_months = 6

    months = iter_months(num_months)

    print(f"\nPR Statistics for {repo} — past {num_months} months")
    print("=" * 80)
    header = f"{'Month':<10}  {'Opened':>8}  {'MoM%':>8}  {'Merged':>8}  {'MoM%':>8}  {'Merge Rate':>10}"
    print(header)
    print("-" * 80)

    prev_opened = None
    prev_merged = None
    total_opened = 0
    total_merged = 0
    rows = []

    for year, month in months:
        label = f"{year}-{month:02d}"
        print(f"  Fetching {label}...", file=sys.stderr)
        opened = count_opened(repo, year, month)
        merged = count_merged(repo, year, month)
        rows.append((label, opened, merged))
        total_opened += opened
        total_merged += merged

    for label, opened, merged in rows:
        if prev_opened is not None and prev_opened > 0:
            opened_mom_str = f"{(opened - prev_opened) / prev_opened * 100:+.1f}%"
        else:
            opened_mom_str = "  N/A"

        if prev_merged is not None and prev_merged > 0:
            merged_mom_str = f"{(merged - prev_merged) / prev_merged * 100:+.1f}%"
        else:
            merged_mom_str = "  N/A"

        merge_rate = f"{merged / opened * 100:.1f}%" if opened > 0 else "N/A"
        print(f"{label:<10}  {opened:>8}  {opened_mom_str:>8}  {merged:>8}  {merged_mom_str:>8}  {merge_rate:>10}")

        prev_opened = opened
        prev_merged = merged

    print("-" * 80)
    total_rate = f"{total_merged / total_opened * 100:.1f}%" if total_opened > 0 else "N/A"
    print(f"{'TOTAL':<10}  {total_opened:>8}  {'':>8}  {total_merged:>8}  {'':>8}  {total_rate:>10}")
    print()

    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    if current_month in [label for label, _, _ in rows]:
        print(f"Note: {current_month} is a partial month (data through today).")


if __name__ == "__main__":
    main()
