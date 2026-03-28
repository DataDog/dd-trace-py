#!/usr/bin/env python3
"""Wait for a GitLab CI job to finish and report its status.

Usage:
    wait_ci.py <job_name_substring> [--branch <branch>] [--interval <seconds>]

    # Uses current git branch by default
    wait_ci.py "upload manylinux2014_x86_64"

    # Explicit branch
    wait_ci.py "upload manylinux2014_x86_64" --branch main

Environment:
    GITLAB_TOKEN  — GitLab personal access token (required)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Optional


try:
    import requests
except ImportError:
    sys.exit("requests is required: pip install requests")

GITLAB_BASE = "https://gitlab.ddbuild.io"
API = f"{GITLAB_BASE}/api/v4"
PROJECT_ID = 358

TERMINAL_STATUSES = {"success", "failed", "canceled", "skipped", "manual"}


def _token() -> str:
    token = os.environ.get("GITLAB_TOKEN", "")
    if not token:
        sys.exit("GITLAB_TOKEN env var is required")
    return token


def _headers() -> dict[str, str]:
    return {"PRIVATE-TOKEN": _token()}


def _current_branch() -> str:
    try:
        return subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    except Exception:
        sys.exit("Could not determine current git branch")


def _current_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sys.exit("Could not determine current commit SHA")


def _get_latest_pipeline(branch: str, sha: Optional[str] = None) -> Optional[dict]:
    params: dict[str, str | int] = {"ref": branch, "per_page": 1, "order_by": "id", "sort": "desc"}
    if sha:
        params["sha"] = sha
    resp = requests.get(
        f"{API}/projects/{PROJECT_ID}/pipelines",
        headers=_headers(),
        params=params,
    )
    resp.raise_for_status()
    pipelines = resp.json()
    return pipelines[0] if pipelines else None


def _get_jobs(pipeline_id: int) -> list[dict]:
    jobs: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{API}/projects/{PROJECT_ID}/pipelines/{pipeline_id}/jobs",
            headers=_headers(),
            params={"per_page": 100, "page": page},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        jobs.extend(batch)
        page += 1
    return jobs


def _find_job(jobs: list[dict], name_sub: str) -> Optional[dict]:
    matches = [j for j in jobs if name_sub.lower() in j["name"].lower()]
    exact_match = [j for j in jobs if name_sub.lower() == j["name"].lower()]
    if not matches:
        return None

    if not exact_match and len(matches) > 1:
        print(f"Multiple matches for '{name_sub}':")
        for j in matches:
            print(f"  - {j['name']}  (id={j['id']}, status={j['status']})")
        sys.exit("Please refine the job name substring.")

    return exact_match[0] if exact_match else matches[0]


def _status_symbol(status: str) -> str:
    return {
        "success": "\033[32m✓\033[0m",
        "failed": "\033[31m✗\033[0m",
        "canceled": "\033[33m⊘\033[0m",
        "skipped": "\033[90m⊘\033[0m",
        "running": "\033[34m⟳\033[0m",
        "pending": "\033[90m◯\033[0m",
        "created": "\033[90m◯\033[0m",
        "manual": "\033[33m▶\033[0m",
    }.get(status, "?")


def _log(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def wait(branch: str, job_name: str, poll_interval: int) -> int:
    sha = _current_commit()
    _log(f"Waiting for pipeline on commit {sha[:12]}...")

    pipeline: Optional[dict] = None
    while pipeline is None:
        pipeline = _get_latest_pipeline(branch, sha=sha)
        if pipeline is None:
            _log(f"No pipeline yet for {sha[:12]}, retrying in {poll_interval}s...")
            time.sleep(poll_interval)

    pipeline_id: int = pipeline["id"]
    print(pipeline_id)

    _log(f"Branch:   {branch}")
    _log(f"Pipeline: {pipeline['web_url']}")
    _log(f"Job:      '{job_name}'")
    _log()

    while True:
        jobs = _get_jobs(pipeline_id)
        job = _find_job(jobs, job_name)
        if job is None:
            available = sorted({j["name"] for j in jobs})
            _log(f"No job matching '{job_name}'. Available jobs:")
            for n in available:
                _log(f"  - {n}")
            return 1

        status = job["status"]
        symbol = _status_symbol(status)
        web_url = job.get("web_url", "")
        ts = time.strftime("%H:%M:%S")
        _log(f"[{ts}] {symbol} {job['name']}: {status}  ({web_url})")

        if status in TERMINAL_STATUSES:
            _log()
            if status == "success":
                _log("\033[32mJob succeeded!\033[0m")
                return 0
            elif status == "failed":
                _log("\033[31mJob failed.\033[0m")
                return 1
            else:
                _log(f"Job ended with status: {status}")
                return 1 if status != "manual" else 0

        time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for a GitLab CI job to complete.")
    parser.add_argument("job", help="Job name (substring match, case-insensitive)")
    parser.add_argument("--branch", "-b", default=None, help="Git branch (default: current branch)")
    parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds (default: 5)")
    args = parser.parse_args()

    branch = args.branch or _current_branch()
    rc = wait(branch, args.job, args.interval)
    sys.exit(rc)


if __name__ == "__main__":
    main()
