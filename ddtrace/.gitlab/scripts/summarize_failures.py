#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx", "claude-agent-sdk"]
# ///
r"""Collect metadata and logs for all failed jobs in a GitLab pipeline, then
invoke Claude Code (via the Datadog AI Gateway) to produce a human-readable
failure summary.

Phase 1 — data collection:
  Writes $CI_FAILURE_LOGS_DIR/failures.json and per-job log files.

Phase 2 — analysis:
  Uses the claude-agent-sdk (bundled native binary) to run an agentic analysis
  against the collected logs. Writes summary.md and claude.stdout.log.

Auth flow:
  1. Download authanywhere and run it (--audience rapid-devex-ci) for a BTI JWT.
  2. Call BTI /gitlab/token to get a short-lived GitLab PAT (api scope).
  3. Use the GitLab PAT with CI_API_V4_URL to list jobs and bridges.
  4. Use the BTI trace endpoint to download individual job logs.
  5. Run authanywhere again (--audience rapid-ai-platform) for the AI Gateway.

Required environment variables (all provided by GitLab CI):
  CI_API_V4_URL, CI_PROJECT_ID, CI_PIPELINE_ID, CI_FAILURE_LOGS_DIR

Optional:
  SUMMARIZE_PIPELINE_ID  override to fetch data from a specific pipeline
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from claude_agent_sdk import AssistantMessage
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk import TextBlock
from claude_agent_sdk import query
import httpx


CI_API_V4_URL = os.environ["CI_API_V4_URL"].rstrip("/")
CI_PROJECT_ID = os.environ["CI_PROJECT_ID"]
# SUMMARIZE_PIPELINE_ID lets you point the job at any existing pipeline
# (e.g. a known failure) without needing to simulate one in the current run.
CI_PIPELINE_ID = os.environ.get("SUMMARIZE_PIPELINE_ID") or os.environ["CI_PIPELINE_ID"]
LOGS_DIR = Path(os.environ["CI_FAILURE_LOGS_DIR"])

# GitHub owner/repo — used for BTI API calls which reference the GitHub identity,
# not the GitLab subgroup path (CI_PROJECT_NAMESPACE would be DataDog/apm-reliability).
GH_OWNER = "DataDog"
GH_REPO = "dd-trace-py"

BTI_BASE_URL = "https://bti-ci-api.us1.ddbuild.io/internal/ci"
AUTHANYWHERE_URL = "https://binaries.ddbuild.io/dd-source/authanywhere/LATEST/authanywhere-linux-amd64"
AI_GATEWAY_BASE_URL = "https://ai-gateway.us1.ddbuild.io"

BTI_AUDIENCE = "rapid-devex-ci"
AI_AUDIENCE = "rapid-ai-platform"

# Tools granted to claude; kept here so they're easy to audit and extend.
CLAUDE_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Bash(grep:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(unzip -l:*)",
    "Bash(file:*)",
    "Bash(ls:*)",
    "Bash(jq:*)",
]

# Statuses that mean a child pipeline has work worth collecting.
FAILED_STATUSES = frozenset({"failed", "canceled"})

# Maps known GitLab project IDs → (GitHub owner, repo) for BTI trace downloads.
# Add entries here as child pipeline projects are identified (check the project's
# GitLab URL for the numeric ID, e.g. gitlab.ddbuild.io/DataDog/foo/-/settings).
KNOWN_PROJECT_REPOS: dict[str, tuple[str, str]] = {
    "358": ("DataDog", "dd-trace-py"),
    "1611": ("DataDog", "datadog-lambda-python"),
    "4339": ("DataDog", "serverless-tools"),
    "6260": ("DataDog", "datadog-packages"),
}


def _bti_headers(bti_auth_header: str) -> dict[str, str]:
    """Strip the 'Authorization: ' prefix authanywhere emits and return a headers dict."""
    return {"Authorization": bti_auth_header.removeprefix("Authorization: ")}


def _bti_repo(project_id: str) -> tuple[str, str] | None:
    """Return (gh_owner, gh_repo) for a known project ID, or None if unknown."""
    owner, repo = KNOWN_PROJECT_REPOS.get(project_id, ("", ""))
    return (owner, repo) if owner else None


def _safe_paginate(client: httpx.Client, url: str, label: str, **params) -> list[dict]:
    """Paginate url, returning [] with a warning on HTTP errors (e.g. foreign-project 404)."""
    try:
        return paginate(client, url, **params)
    except httpx.HTTPStatusError as exc:
        print(f"  [warn] Cannot list {label}: HTTP {exc.response.status_code}", file=sys.stderr)
        return []


def get_authanywhere_bin(tmpdir: str) -> str:
    """Download authanywhere on Linux if needed; return its path."""
    if platform.system() == "Linux":
        binary = Path(tmpdir) / "authanywhere-linux-amd64"
        if not binary.exists():
            print("Downloading authanywhere...", file=sys.stderr)
            r = httpx.get(AUTHANYWHERE_URL, follow_redirects=True, timeout=30)
            r.raise_for_status()
            binary.write_bytes(r.content)
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        return str(binary)
    cmd = shutil.which("authanywhere")
    if not cmd:
        raise RuntimeError("authanywhere not found in PATH; install it via Homebrew or dd-source")
    return cmd


def authanywhere_header(bin_path: str, audience: str) -> str:
    result = subprocess.run(
        [bin_path, "--audience", audience],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_gitlab_token(bti_auth_header: str) -> str:
    r = httpx.get(
        f"{BTI_BASE_URL}/gitlab/token",
        params={"owner": GH_OWNER, "repository": GH_REPO},
        headers=_bti_headers(bti_auth_header),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)[:80]


def _job_dir(pipeline_id: str) -> Path:
    d = LOGS_DIR / pipeline_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unpack_downstream(bridge: dict, default_project_id: str) -> tuple[str, str, str, str] | None:
    downstream = bridge.get("downstream_pipeline")
    if not downstream or downstream.get("id") is None:
        return None
    return (
        str(downstream["id"]),
        str(downstream.get("project_id", default_project_id)),
        downstream.get("status", "unknown"),
        downstream.get("web_url", ""),
    )


def paginate(client: httpx.Client, url: str, **params) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        r = client.get(url, params={**params, "per_page": 100, "page": page})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return results


def get_pipeline(client: httpx.Client, bti_auth_header: str, project_id: str, pipeline_id: str) -> dict:
    coords = _bti_repo(project_id)
    if coords:
        gh_owner, gh_repo = coords
        r = httpx.get(
            f"{BTI_BASE_URL}/gitlab/pipeline/{gh_owner}/{gh_repo}/{pipeline_id}",
            headers=_bti_headers(bti_auth_header),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["pipeline"]
    else:
        r = client.get(f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}")
        r.raise_for_status()
        return r.json()


def get_failed_jobs(client: httpx.Client, project_id: str, pipeline_id: str) -> list[dict]:
    jobs = _safe_paginate(
        client,
        f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        f"jobs for pipeline {pipeline_id} in project {project_id} — PAT may lack access",
        include_retried="true",
    )
    # GitLab returns jobs newest-first; keep the first (latest) occurrence of each name.
    seen: set[str] = set()
    failed: list[dict] = []
    for job in jobs:
        name = job["name"]
        if name not in seen:
            seen.add(name)
            if job["status"] == "failed":
                failed.append(job)
    return failed


def get_bridges(client: httpx.Client, project_id: str, pipeline_id: str) -> list[dict]:
    return _safe_paginate(
        client,
        f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}/bridges",
        f"bridges for pipeline {pipeline_id} in project {project_id}",
    )


def download_trace(
    client: httpx.Client, bti_auth_header: str, project_id: str, job: dict, pipeline_id: str
) -> str | None:
    job_id = job["id"]
    job_name = _safe_name(job["name"])
    log_path = _job_dir(pipeline_id) / f"{job_id}-{job_name}.log"

    coords = _bti_repo(project_id)
    try:
        if coords:
            gh_owner, gh_repo = coords
            r = httpx.get(
                f"{BTI_BASE_URL}/gitlab/repository/{gh_owner}/{gh_repo}/jobs/{job_id}/trace",
                headers=_bti_headers(bti_auth_header),
                timeout=60,
            )
            if r.status_code == 404:
                print(f"  [warn] No trace for job {job_id} ({job['name']})", file=sys.stderr)
                return None
            r.raise_for_status()
            log_path.write_text(r.json().get("trace", ""), encoding="utf-8")
        else:
            # Unknown project — try GitLab API directly (may 403 for external projects).
            r = client.get(f"{CI_API_V4_URL}/projects/{project_id}/jobs/{job_id}/trace", timeout=60)
            if r.status_code in (403, 404):
                print(
                    f"  [warn] No trace for job {job_id} ({job['name']}) in project {project_id}"
                    f" — add to KNOWN_PROJECT_REPOS to enable BTI fetch",
                    file=sys.stderr,
                )
                return None
            r.raise_for_status()
            log_path.write_text(r.text, encoding="utf-8")
        return str(log_path)
    except Exception as exc:
        print(f"  [warn] Failed to download trace for job {job_id}: {exc}", file=sys.stderr)
        return None


def download_artifacts(client: httpx.Client, project_id: str, job: dict, pipeline_id: str) -> str | None:
    job_id = job["id"]
    job_name = _safe_name(job["name"])
    artifact_path = _job_dir(pipeline_id) / f"{job_id}-{job_name}-artifacts.zip"

    try:
        with client.stream(
            "GET",
            f"{CI_API_V4_URL}/projects/{project_id}/jobs/{job_id}/artifacts",
            timeout=120,
            follow_redirects=True,
        ) as r:
            if r.status_code in (404, 403):
                return None
            r.raise_for_status()
            with open(artifact_path, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=65536):
                    f.write(chunk)
        return str(artifact_path)
    except Exception as exc:
        print(f"  [warn] Failed to download artifacts for job {job_id}: {exc}", file=sys.stderr)
        return None


def collect_for_pipeline(
    client: httpx.Client,
    bti_auth_header: str,
    project_id: str,
    pipeline_id: str,
    depth: int = 0,
) -> tuple[dict, list[dict], list[dict]]:
    indent = "  " * depth
    print(f"{indent}Collecting pipeline {pipeline_id}...", file=sys.stderr)
    pipeline = get_pipeline(client, bti_auth_header, project_id, pipeline_id)

    failed_jobs = get_failed_jobs(client, project_id, pipeline_id)
    print(f"{indent}  {len(failed_jobs)} failed job(s)", file=sys.stderr)

    all_failed_jobs: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        trace_futs = {
            job["id"]: executor.submit(download_trace, client, bti_auth_header, project_id, job, pipeline_id)
            for job in failed_jobs
        }
        artifact_futs = {
            job["id"]: executor.submit(download_artifacts, client, project_id, job, pipeline_id) for job in failed_jobs
        }
        for job in failed_jobs:
            print(f"{indent}  Downloading: {job['name']}", file=sys.stderr)
            log_path = trace_futs[job["id"]].result()
            artifact_path = artifact_futs[job["id"]].result()
            log_size = Path(log_path).stat().st_size if log_path else 0
            all_failed_jobs.append(
                {
                    "id": job["id"],
                    "name": job["name"],
                    "stage": job["stage"],
                    "web_url": job.get("web_url", ""),
                    "failure_reason": job.get("failure_reason"),
                    "duration": job.get("duration"),
                    "retried": job.get("retried", False),
                    "pipeline_id": pipeline_id,
                    "log_path": log_path,
                    "log_size_bytes": log_size,
                    "artifact_path": artifact_path,
                }
            )

    bridges = get_bridges(client, project_id, pipeline_id)
    child_pipeline_infos: list[dict] = []
    for bridge in bridges:
        unpacked = _unpack_downstream(bridge, project_id)
        if not unpacked:
            continue
        child_pid, child_project_id, child_status, child_url = unpacked
        child_pipeline_infos.append({"id": child_pid, "status": child_status, "web_url": child_url})

        if child_status in FAILED_STATUSES:
            try:
                _, child_jobs, _ = collect_for_pipeline(client, bti_auth_header, child_project_id, child_pid, depth + 1)
                all_failed_jobs.extend(child_jobs)
            except httpx.HTTPStatusError as exc:
                print(
                    f"{indent}  [warn] Cannot access child pipeline {child_pid} "
                    f"(project {child_project_id}): HTTP {exc.response.status_code}",
                    file=sys.stderr,
                )

    pipeline_summary = {
        "id": pipeline["id"],
        "ref": pipeline.get("ref"),
        "sha": pipeline.get("sha"),
        "web_url": pipeline.get("web_url", ""),
        "status": "failed",  # always failed — this job runs when: on_failure
        "source": pipeline.get("source"),
    }
    return pipeline_summary, all_failed_jobs, child_pipeline_infos


async def _analyze(ai_auth_header: str, project_dir: Path) -> None:
    system_prompt_path = project_dir / ".gitlab" / "scripts" / "summarize-failures.system.md"
    if not system_prompt_path.exists():
        print(f"[warn] System prompt not found at {system_prompt_path}; skipping analysis", file=sys.stderr)
        return

    # Merge os.environ so PATH and other essentials are available to the bundled binary.
    # authanywhere outputs "Authorization: Bearer <token>"; gateway reads it from ANTHROPIC_CUSTOM_HEADERS.
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "not-set",
        "ANTHROPIC_BASE_URL": AI_GATEWAY_BASE_URL,
        "ANTHROPIC_CUSTOM_HEADERS": (
            f"source: claude-code\norg-id: 2\nprovider: anthropic\nclaude-code: true\n{ai_auth_header}"
        ),
    }

    prompt = (
        "Summarize CI failures in ci-failure-logs/failures.json. "
        "Read logs from ci-failure-logs/, correlate against the source tree, "
        "write summary.md."
    )

    log_path = project_dir / "claude.stdout.log"
    print("Running claude analysis...", file=sys.stderr)
    with open(log_path, "w") as log_file:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=system_prompt_path.read_text(),
                allowed_tools=CLAUDE_ALLOWED_TOOLS,
                cwd=str(project_dir),
                env=env,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        log_file.write(block.text)

    summary = project_dir / "summary.md"
    if summary.exists():
        print(f"\n{'=' * 60}", flush=True)
        print(summary.read_text(), flush=True)
        print(f"{'=' * 60}\n", flush=True)
    else:
        log_content = log_path.read_text()
        print("[warn] Claude did not produce summary.md", file=sys.stderr)
        print(log_content[:2000] if log_content else "(no output)", file=sys.stderr)


def run_claude_analysis(ai_auth_header: str, project_dir: Path) -> None:
    asyncio.run(_analyze(ai_auth_header, project_dir))


def print_summary(pipeline: dict, failed_jobs: list[dict], child_pipelines: list[dict]) -> None:
    print(f"\n=== Pipeline {pipeline['id']} ({pipeline['ref']}) — {pipeline['status']} ===")
    print(f"    {pipeline['web_url']}")

    print(f"\n=== Failed jobs ({len(failed_jobs)}) ===")
    for job in failed_jobs:
        print(f"  [{job['stage']}] {job['name']}")
        print(f"    failure_reason : {job['failure_reason'] or 'unknown'}")
        duration = f"{job['duration']:.0f}s" if job["duration"] else "n/a"
        print(f"    duration       : {duration}")
        print(f"    log            : {job['log_path'] or 'n/a'} ({job['log_size_bytes']} bytes)")
        print(f"    artifacts      : {job['artifact_path'] or 'none'}")
        print(f"    url            : {job['web_url']}")

    if child_pipelines:
        print(f"\n=== Child pipelines ({len(child_pipelines)}) ===")
        for cp in child_pipelines:
            print(f"  [{cp['status']}] {cp['web_url']}")


def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    project_dir = Path(os.environ.get("CI_PROJECT_DIR", ".")).resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        authanywhere_bin = get_authanywhere_bin(tmpdir)

        # Fetch both auth tokens in parallel — they're independent network calls.
        with ThreadPoolExecutor(max_workers=2) as ex:
            bti_fut = ex.submit(authanywhere_header, authanywhere_bin, BTI_AUDIENCE)
            ai_fut = ex.submit(authanywhere_header, authanywhere_bin, AI_AUDIENCE)
            bti_auth_header = bti_fut.result()
            ai_auth_header = ai_fut.result()

        gitlab_token = get_gitlab_token(bti_auth_header)
        gitlab_headers = {"PRIVATE-TOKEN": gitlab_token}

        with httpx.Client(headers=gitlab_headers, timeout=30) as client:
            pipeline_summary, failed_jobs, child_pipelines = collect_for_pipeline(
                client, bti_auth_header, CI_PROJECT_ID, CI_PIPELINE_ID
            )

        if not failed_jobs:
            print("No failed jobs found — nothing to summarize.")
            sys.exit(0)

        output = {
            "pipeline": pipeline_summary,
            "child_pipelines": child_pipelines,
            "failed_jobs": failed_jobs,
        }
        failures_json = LOGS_DIR / "failures.json"
        failures_json.write_text(json.dumps(output, indent=2))
        print(f"Wrote {failures_json}", file=sys.stderr)

        print_summary(pipeline_summary, failed_jobs, child_pipelines)
        print(f"\nArtifacts: {LOGS_DIR}/")

        run_claude_analysis(ai_auth_header, project_dir)


if __name__ == "__main__":
    main()
