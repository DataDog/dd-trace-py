#!/usr/bin/env python3
"""Fetch profiling_native job durations from a single GitLab pipeline.

Requires:
    export GITLAB_TOKEN=glpat-...   (read_api scope on gitlab.ddbuild.io)

Usage:
    python3 fetch_single_pipeline.py 96940191
"""
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
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pipeline_id>", file=sys.stderr)
        sys.exit(1)

    pipeline_id = sys.argv[1]
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        print("Error: set GITLAB_TOKEN environment variable (read_api scope)", file=sys.stderr)
        sys.exit(1)

    # Fetch all jobs from the pipeline
    page = 1
    native_jobs = []
    while True:
        jobs = api(token, f"{BASE}/pipelines/{pipeline_id}/jobs?per_page=100&page={page}")
        if not isinstance(jobs, list) or not jobs:
            break
        for j in jobs:
            if "profiling_native" in j.get("name", ""):
                dur = j.get("duration") or 0
                native_jobs.append({"name": j["name"], "duration": dur, "status": j.get("status", "?")})
        if len(jobs) < 100:
            break
        page += 1

    native_jobs.sort(key=lambda j: j["name"])

    total = sum(j["duration"] for j in native_jobs)
    print(f"Pipeline #{pipeline_id}: {len(native_jobs)} profiling_native jobs\n")
    print(f"{'Job':<60} {'Duration':>10}")
    print(f"{'-'*60} {'-'*10}")

    for j in native_jobs:
        print(f"{j['name']:<60} {fmt_duration(j['duration']):>10}")

    print(f"\n{'Total':<60} {fmt_duration(total):>10}")
    print(f"{'Average':<60} {fmt_duration(total / len(native_jobs) if native_jobs else 0):>10}")


if __name__ == "__main__":
    main()
