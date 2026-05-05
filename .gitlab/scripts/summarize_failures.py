#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = ["httpx"]
# ///
r"""Collect metadata and logs for all failed jobs in a GitLab pipeline, then
invoke Claude Code (via the Datadog AI Gateway) to produce a human-readable
failure summary.

Phase 1 — data collection:
  Writes $CI_FAILURE_LOGS_DIR/failures.json and per-job log files.

Phase 2 — analysis:
  Installs the claude CLI via npm, then runs `claude -p` agentically against
  the collected logs. Writes summary.md and claude.stdout.log.

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


def get_authanywhere_bin(tmpdir: str) -> str:
    """Return path to authanywhere binary, downloading it on Linux if needed."""
    if platform.system() == "Linux":
        binary = Path(tmpdir) / "authanywhere-linux-amd64"
        if not binary.exists():
            print("Downloading authanywhere...", file=sys.stderr)
            r = httpx.get(AUTHANYWHERE_URL, follow_redirects=True, timeout=30)
            r.raise_for_status()
            binary.write_bytes(r.content)
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        return str(binary)
    else:
        cmd = shutil.which("authanywhere")
        if not cmd:
            raise RuntimeError("authanywhere not found in PATH; install it via Homebrew or dd-source")
        return cmd


def authanywhere_header(bin_path: str, audience: str) -> str:
    """Call authanywhere for the given audience; return the Authorization header string."""
    result = subprocess.run(
        [bin_path, "--audience", audience],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_gitlab_token(bti_auth_header: str) -> str:
    """Use BTI to obtain a short-lived GitLab PAT with api scope."""
    r = httpx.get(
        f"{BTI_BASE_URL}/gitlab/token",
        params={"owner": GH_OWNER, "repository": GH_REPO},
        headers={"Authorization": bti_auth_header.removeprefix("Authorization: ")},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)[:80]


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
    """Fetch pipeline details via BTI for the main repo, GitLab API for child pipelines."""
    if project_id == CI_PROJECT_ID:
        r = httpx.get(
            f"{BTI_BASE_URL}/gitlab/pipeline/{GH_OWNER}/{GH_REPO}/{pipeline_id}",
            headers={"Authorization": bti_auth_header.removeprefix("Authorization: ")},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["pipeline"]
    else:
        r = client.get(f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}")
        r.raise_for_status()
        return r.json()


def get_failed_jobs(client: httpx.Client, project_id: str, pipeline_id: str) -> list[dict]:
    jobs = paginate(
        client,
        f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        include_retried="true",
    )
    # Keep only the latest attempt for each job name that ended in failure.
    # GitLab returns jobs newest-first so the first occurrence of a name is
    # the most recent attempt.
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
    return paginate(
        client,
        f"{CI_API_V4_URL}/projects/{project_id}/pipelines/{pipeline_id}/bridges",
    )


def download_trace(bti_auth_header: str, job: dict, pipeline_id: str) -> str | None:
    """Fetch job log via the BTI trace endpoint (returns JSON {"trace": "..."})."""
    job_id = job["id"]
    job_name = _safe_name(job["name"])
    log_dir = LOGS_DIR / str(pipeline_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}-{job_name}.log"

    try:
        r = httpx.get(
            f"{BTI_BASE_URL}/gitlab/repository/{GH_OWNER}/{GH_REPO}/jobs/{job_id}/trace",
            headers={"Authorization": bti_auth_header.removeprefix("Authorization: ")},
            timeout=60,
        )
        if r.status_code == 404:
            print(f"  [warn] No trace for job {job_id} ({job['name']})", file=sys.stderr)
            return None
        r.raise_for_status()
        log_path.write_text(r.json().get("trace", ""), encoding="utf-8")
        return str(log_path)
    except Exception as exc:
        print(f"  [warn] Failed to download trace for job {job_id}: {exc}", file=sys.stderr)
        return None


def download_artifacts(client: httpx.Client, project_id: str, job: dict, pipeline_id: str) -> str | None:
    """Fetch job artifacts zip via the GitLab API."""
    job_id = job["id"]
    job_name = _safe_name(job["name"])
    artifact_dir = LOGS_DIR / str(pipeline_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{job_id}-{job_name}-artifacts.zip"

    try:
        r = client.get(
            f"{CI_API_V4_URL}/projects/{project_id}/jobs/{job_id}/artifacts",
            timeout=120,
            follow_redirects=True,
        )
        if r.status_code in (404, 403):
            return None
        r.raise_for_status()
        artifact_path.write_bytes(r.content)
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
    for job in failed_jobs:
        print(f"{indent}  Downloading trace: {job['name']}", file=sys.stderr)
        log_path = download_trace(bti_auth_header, job, pipeline_id)
        artifact_path = download_artifacts(client, project_id, job, pipeline_id)
        log_size = Path(log_path).stat().st_size if log_path and Path(log_path).exists() else 0

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
        downstream = bridge.get("downstream_pipeline")
        if not downstream or downstream.get("id") is None:
            continue
        child_pid = str(downstream["id"])
        child_project_id = str(downstream.get("project_id", project_id))
        child_status = downstream.get("status", "unknown")
        child_url = downstream.get("web_url", "")

        child_pipeline_infos.append({"id": child_pid, "status": child_status, "web_url": child_url})

        if child_status in ("failed", "canceled"):
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


NODE_VERSION = "v22.14.0"  # Node 22 LTS
NODE_URL = f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-linux-x64.tar.xz"


def get_claude(tmpdir: str) -> tuple[str, str]:
    """Return (claude_bin_path, node_bin_dir) installing node+npm+claude if needed.

    node_bin_dir must be prepended to PATH when running claude so that its
    shebang (#!/usr/bin/env node) resolves correctly.

    On Linux (CI) downloads node and installs claude to a local prefix inside
    tmpdir to avoid needing root. On other platforms returns claude from PATH
    with an empty node_bin_dir (node is already on PATH).
    """
    if platform.system() != "Linux":
        cmd = shutil.which("claude")
        if not cmd:
            raise RuntimeError("claude not found in PATH; install via: npm install -g @anthropic-ai/claude-code")
        return cmd, ""

    npm_prefix = Path(tmpdir) / "npm-global"
    claude_bin = npm_prefix / "bin" / "claude"

    npm = shutil.which("npm")
    node_bin_dir = str(Path(npm).parent) if npm else ""

    if not claude_bin.exists():
        if not npm:
            node_dir = Path(tmpdir) / "node"
            node_dir.mkdir(exist_ok=True)
            print(f"Downloading node {NODE_VERSION}...", file=sys.stderr)
            r = httpx.get(NODE_URL, follow_redirects=True, timeout=120)
            r.raise_for_status()
            tarball = Path(tmpdir) / "node.tar.xz"
            tarball.write_bytes(r.content)
            subprocess.run(
                ["tar", "-xJ", "--strip-components", "1", "-C", str(node_dir), "-f", str(tarball)],
                check=True,
            )
            npm = str(node_dir / "bin" / "npm")
            node_bin_dir = str(node_dir / "bin")

        print("Installing claude CLI...", file=sys.stderr)
        install_env = os.environ.copy()
        install_env["PATH"] = f"{node_bin_dir}:{install_env.get('PATH', '')}"
        subprocess.run(
            [npm, "install", "-g", "@anthropic-ai/claude-code", "--prefix", str(npm_prefix)],
            check=True,
            text=True,
            env=install_env,
        )
        if not claude_bin.exists():
            raise RuntimeError(f"claude not found at {claude_bin} after npm install")

    return str(claude_bin), node_bin_dir


def run_claude_analysis(claude_bin: str, node_bin_dir: str, authanywhere_bin: str, project_dir: Path) -> None:
    """Run claude -p agentically to analyze failures and write summary.md.

    Uses authanywhere (--audience rapid-ai-platform) for AI Gateway auth so
    no keyring or ddtool setup is required in CI.
    """
    system_prompt_path = project_dir / ".gitlab" / "scripts" / "summarize-failures.system.md"
    if not system_prompt_path.exists():
        print(f"[warn] System prompt not found at {system_prompt_path}; skipping analysis", file=sys.stderr)
        return

    print("Getting AI Gateway token...", file=sys.stderr)
    ai_auth_header = authanywhere_header(authanywhere_bin, "rapid-ai-platform")

    prompt = (
        "Summarize CI failures in ci-failure-logs/failures.json. "
        "Read logs from ci-failure-logs/, correlate against the source tree, "
        "write summary.md."
    )
    allowed_tools = ",".join(
        [
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
    )

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "not-set"
    env["ANTHROPIC_BASE_URL"] = AI_GATEWAY_BASE_URL
    # authanywhere outputs "Authorization: Bearer <token>"; append as the last
    # custom header so the gateway can authenticate the request.
    env["ANTHROPIC_CUSTOM_HEADERS"] = (
        f"source: claude-code\norg-id: 2\nprovider: anthropic\nclaude-code: true\n{ai_auth_header}"
    )
    if node_bin_dir:
        env["PATH"] = f"{node_bin_dir}:{env.get('PATH', '')}"

    log_path = project_dir / "claude.stdout.log"
    print("Running claude analysis...", file=sys.stderr)
    with open(log_path, "w") as log_file:
        result = subprocess.run(
            [
                claude_bin,
                "-p",
                "--output-format",
                "text",
                "--system-prompt",
                system_prompt_path.read_text(),
                "--allowed-tools",
                allowed_tools,
            ],
            input=prompt,
            cwd=str(project_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
        )
        log_file.write(result.stdout)

    summary = project_dir / "summary.md"
    if summary.exists():
        print(f"\n{'=' * 60}", flush=True)
        print(summary.read_text(), flush=True)
        print(f"{'=' * 60}\n", flush=True)
    else:
        print("[warn] Claude did not produce summary.md", file=sys.stderr)
        print(result.stdout[:2000] if result.stdout else "(no output)", file=sys.stderr)


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
        bti_auth_header = authanywhere_header(authanywhere_bin, "rapid-devex-ci")
        claude_bin, node_bin_dir = get_claude(tmpdir)

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

        run_claude_analysis(claude_bin, node_bin_dir, authanywhere_bin, project_dir)


if __name__ == "__main__":
    main()
