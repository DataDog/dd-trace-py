#!/usr/bin/env python3
"""Fetch actual profiling_native job durations from GitLab for a list of commits.

Requires:
    export GITLAB_TOKEN=glpat-...   (read_api scope on gitlab.ddbuild.io)

Usage:
    # First, generate the list of skippable commits:
    python3 find_skippable_commits.py

    # Then fetch their job durations:
    python3 fetch_job_durations.py [--shas /tmp/skipped_shas.txt]

Outputs:
    - Per-pipeline and aggregate summary to stdout
    - /tmp/native_job_results.json with full data
"""
import argparse
import json
import os
import subprocess
import sys

GITLAB_HOST = "https://gitlab.ddbuild.io"
PROJECT_PATH = "datadog%2Fapm-reliability%2Fdd-trace-py"
BASE = f"{GITLAB_HOST}/api/v4/projects/{PROJECT_PATH}"


def api(token: str, url: str) -> dict | list:
    result = subprocess.run(
        ["curl", "-s", "-H", f"PRIVATE-TOKEN: {token}", url],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shas", default="/tmp/skipped_shas.txt", help="File with one SHA per line")
    parser.add_argument("--output", default="/tmp/native_job_results.json", help="Output JSON file")
    args = parser.parse_args()

    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        print("Error: set GITLAB_TOKEN environment variable (read_api scope)", file=sys.stderr)
        sys.exit(1)

    with open(args.shas) as f:
        shas = [line.strip() for line in f if line.strip()]

    print(f"Looking up pipelines for {len(shas)} commits...\n")

    # Step 1: resolve SHAs to pipeline IDs
    pipelines: dict[str, int | None] = {}
    for i, sha in enumerate(shas, 1):
        data = api(token, f"{BASE}/pipelines?sha={sha}&ref=main&per_page=1")
        pipelines[sha] = data[0]["id"] if isinstance(data, list) and data else None
        if i % 10 == 0:
            print(f"  Resolved {i}/{len(shas)} pipelines...")

    found = {k: v for k, v in pipelines.items() if v is not None}
    missing = len(shas) - len(found)
    if missing:
        print(f"  Warning: {missing} commits had no pipeline on main")
    print()

    # Step 2: fetch profiling_native jobs from each pipeline
    print(f"Fetching profiling_native jobs from {len(found)} pipelines...\n")

    grand_total_duration = 0.0
    grand_total_jobs = 0
    pipeline_results = []

    for i, (sha, pid) in enumerate(found.items(), 1):
        page = 1
        native_jobs = []
        while True:
            jobs = api(token, f"{BASE}/pipelines/{pid}/jobs?per_page=100&page={page}")
            if not isinstance(jobs, list) or not jobs:
                break
            for j in jobs:
                if "profiling_native" in j.get("name", ""):
                    dur = j.get("duration") or 0
                    native_jobs.append({"name": j["name"], "duration": dur, "status": j.get("status", "?")})
            if len(jobs) < 100:
                break
            page += 1

        pipeline_dur = sum(j["duration"] for j in native_jobs)
        pipeline_results.append({
            "sha": sha[:10],
            "pipeline_id": pid,
            "job_count": len(native_jobs),
            "total_duration_s": pipeline_dur,
        })
        grand_total_duration += pipeline_dur
        grand_total_jobs += len(native_jobs)

        if i % 10 == 0:
            print(f"  Processed {i}/{len(found)} pipelines... ({grand_total_jobs} jobs so far)")

    # Step 3: print results
    with_jobs = [p for p in pipeline_results if p["job_count"] > 0]
    without_jobs = [p for p in pipeline_results if p["job_count"] == 0]

    print(f"\n{'='*70}")
    print(f"{'Pipeline':<12} {'Jobs':>5} {'Duration':>12} {'Avg/job':>10}")
    print(f"{'-'*12} {'-'*5:>5} {'-'*12:>12} {'-'*10:>10}")

    for r in pipeline_results:
        avg = r["total_duration_s"] / r["job_count"] if r["job_count"] > 0 else 0
        print(f"{r['pipeline_id']:<12} {r['job_count']:>5} {fmt_duration(r['total_duration_s']):>12} {fmt_duration(avg):>10}")

    print(f"{'='*70}")
    print(f"\nPipelines with jobs: {len(with_jobs)}")
    print(f"Pipelines without jobs: {len(without_jobs)}")
    print(f"Total profiling_native jobs: {grand_total_jobs}")
    print(f"Total CI-time: {grand_total_duration/3600:.1f} hours ({grand_total_duration/60:.0f} min)")
    if grand_total_jobs > 0:
        print(f"Average per job: {fmt_duration(grand_total_duration / grand_total_jobs)}")
    if with_jobs:
        print(f"Average per trigger: {fmt_duration(grand_total_duration / len(with_jobs))}")
    print(f"Annualized (× {52/6:.1f}): {grand_total_duration/3600 * 52/6:.0f} CI-hours/year")

    # Save results
    with open(args.output, "w") as f:
        json.dump(
            {
                "pipelines": pipeline_results,
                "grand_total_seconds": grand_total_duration,
                "grand_total_jobs": grand_total_jobs,
            },
            f,
            indent=2,
        )
    print(f"\nDetailed results saved to {args.output}")


if __name__ == "__main__":
    main()
