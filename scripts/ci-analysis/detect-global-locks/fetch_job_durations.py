#!/usr/bin/env python3 -u
"""Fetch detect-global-locks job durations from GitLab for skippable commits.
Optimized: only looks at run-tests-trigger child pipelines, paginates fully.
"""
import json
import os
import subprocess
import sys

GITLAB_HOST = "https://gitlab.ddbuild.io"
PROJECT_PATH = "datadog%2Fapm-reliability%2Fdd-trace-py"
BASE = f"{GITLAB_HOST}/api/v4/projects/{PROJECT_PATH}"


def api(token: str, url: str):
    result = subprocess.run(
        ["curl", "-s", "-H", f"PRIVATE-TOKEN: {token}", url],
        capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def main():
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        print("Error: set GITLAB_TOKEN", file=sys.stderr)
        sys.exit(1)

    with open("/tmp/ci-analysis-global-locks/skipped_shas.txt") as f:
        shas = [l.strip() for l in f if l.strip()]

    print(f"Processing {len(shas)} commits...", flush=True)

    total_duration = 0.0
    total_jobs = 0
    pipelines_with_jobs = 0
    pipelines_checked = 0

    for i, sha in enumerate(shas, 1):
        # Resolve SHA to pipeline
        data = api(token, f"{BASE}/pipelines?sha={sha}&ref=main&per_page=1")
        if not isinstance(data, list) or not data:
            continue
        pid = data[0]["id"]

        # Find run-tests-trigger child pipeline
        bridges = api(token, f"{BASE}/pipelines/{pid}/bridges?per_page=100")
        child_pid = None
        if isinstance(bridges, list):
            for b in bridges:
                if "run-tests-trigger" in b.get("name", ""):
                    dp = b.get("downstream_pipeline", {})
                    if dp:
                        child_pid = dp["id"]
                    break

        if not child_pid:
            continue

        # Paginate through child pipeline jobs to find detect-global-locks
        gl_jobs = []
        page = 1
        while True:
            jobs = api(token, f"{BASE}/pipelines/{child_pid}/jobs?per_page=100&page={page}")
            if not isinstance(jobs, list) or not jobs:
                break
            for j in jobs:
                if "detect-global-locks" in j.get("name", ""):
                    dur = j.get("duration") or 0
                    gl_jobs.append(dur)
            if len(jobs) < 100:
                break
            page += 1

        pipelines_checked += 1
        if gl_jobs:
            pipelines_with_jobs += 1
            dur_sum = sum(gl_jobs)
            total_duration += dur_sum
            total_jobs += len(gl_jobs)

        if i % 20 == 0:
            print(f"  [{i}/{len(shas)}] checked={pipelines_checked} with_jobs={pipelines_with_jobs} jobs={total_jobs} time={total_duration/3600:.1f}h", flush=True)

    # Results
    weeks = 6.0
    annualized = total_duration / 3600 * 52 / weeks

    print(f"\n{'='*60}", flush=True)
    print(f"Commits analyzed:           {len(shas)}")
    print(f"Pipelines found:            {pipelines_checked}")
    print(f"Pipelines with jobs:        {pipelines_with_jobs}")
    print(f"Total detect-global-locks:  {total_jobs} jobs")
    print(f"Total CI-time:              {total_duration/3600:.1f} hours")
    if total_jobs > 0:
        print(f"Average per job:            {fmt(total_duration / total_jobs)}")
    print(f"Average per pipeline:       {fmt(total_duration / max(pipelines_with_jobs,1))}")
    print(f"Annualized savings:         ~{annualized:.0f} CI-hours/year")
    print(f"{'='*60}", flush=True)

    with open("/tmp/ci-analysis-global-locks/results.json", "w") as f:
        json.dump({
            "commits": len(shas),
            "pipelines_found": pipelines_checked,
            "pipelines_with_jobs": pipelines_with_jobs,
            "total_jobs": total_jobs,
            "total_duration_s": total_duration,
            "annualized_hours": round(annualized, 1),
        }, f, indent=2)


if __name__ == "__main__":
    main()
