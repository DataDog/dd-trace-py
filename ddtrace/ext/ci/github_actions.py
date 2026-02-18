"""GitHub Actions CI provider integration."""

import glob
import json
import os
import platform
import re
from typing import MutableMapping
from typing import Optional

from ddtrace.ext import git
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# CI tag constants (string literals matching ddtrace.ext.ci constants)
_JOB_ID = "ci.job.id"
_JOB_NAME = "ci.job.name"
_JOB_URL = "ci.job.url"
_PIPELINE_ID = "ci.pipeline.id"
_PIPELINE_NAME = "ci.pipeline.name"
_PIPELINE_NUMBER = "ci.pipeline.number"
_PIPELINE_URL = "ci.pipeline.url"
_PROVIDER_NAME = "ci.provider.name"
_WORKSPACE_PATH = "ci.workspace_path"
_CI_ENV_VARS = "_dd.ci.env_vars"

# Regex to strip credentials from URLs
_RE_URL = re.compile(r"(https?://|ssh://)[^/]*@")


def _filter_sensitive_info(url: Optional[str]) -> Optional[str]:
    return _RE_URL.sub("\\1", url) if url is not None else None


# GitHub Actions job ID resolution constants
JOB_CHECK_RUN_ID_ENV = "JOB_CHECK_RUN_ID"
MAX_DIAG_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# GitHub Actions diagnostics directories by OS
DIAG_DIRS_LINUX = [
    "/home/runner/actions-runner/cached/_diag",
    "/home/runner/actions-runner/_diag",
]

DIAG_DIRS_DARWIN = [
    "/Users/runner/actions-runner/cached/_diag",
    "/Users/runner/actions-runner/_diag",
]

# Regex to extract check_run_id from Worker log files
CHECK_RUN_ID_REGEX = re.compile(r'"k"\s*:\s*"check_run_id"\s*,\s*"v"\s*:\s*([0-9]+)(?:\.0)?')


def _get_diag_dirs() -> list[str]:
    """Get OS-specific diagnostics directory paths."""
    system = platform.system()
    if system == "Windows":
        candidates = []
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates.extend(
                [
                    os.path.join(program_files, "actions-runner", "cached", "_diag"),
                    os.path.join(program_files, "actions-runner", "_diag"),
                ]
            )
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if program_files_x86:
            candidates.extend(
                [
                    os.path.join(program_files_x86, "actions-runner", "cached", "_diag"),
                    os.path.join(program_files_x86, "actions-runner", "_diag"),
                ]
            )
        candidates.extend(
            [
                r"C:\actions-runner\cached\_diag",
                r"C:\actions-runner\_diag",
            ]
        )
        return list(dict.fromkeys(c for c in candidates if c))
    elif system == "Darwin":
        return DIAG_DIRS_DARWIN
    else:
        return DIAG_DIRS_LINUX


def _try_extract_job_id_from_file(file_path: str) -> Optional[str]:
    """Extract job ID from a single Worker log file using JSON parsing with regex fallback."""
    try:
        file_stat = os.stat(file_path)
        if file_stat.st_size > MAX_DIAG_FILE_SIZE:
            log.debug("Skipping oversized diagnostics file %s (%d bytes)", file_path, file_stat.st_size)
            return None

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Try JSON parsing first
        try:
            data = json.loads(content)
            for item in data.get("job", {}).get("d", []):
                if item.get("k") == "check_run_id":
                    value = item.get("v")
                    if isinstance(value, (int, float)):
                        if isinstance(value, float) and value != int(value):
                            continue
                        job_id = str(int(value))
                    elif isinstance(value, str):
                        job_id = value.strip()
                    else:
                        continue
                    if job_id.isdigit():
                        log.debug("Extracted github actions job id via JSON: %s from %s", job_id, file_path)
                        return job_id
        except (ValueError, KeyError, TypeError):
            pass

        # Fall back to regex extraction
        match = CHECK_RUN_ID_REGEX.search(content)
        if match:
            job_id = match.group(1)
            log.debug("Extracted github actions job id via regex: %s from %s", job_id, file_path)
            return job_id

    except OSError as e:
        log.debug("Error reading file %s: %s", file_path, e)

    return None


def _try_extract_job_id_from_diag(diag_dirs: list[str]) -> Optional[str]:
    """Attempt to extract job ID from GitHub Actions diagnostics files."""
    for diag_dir in diag_dirs:
        if not os.path.isdir(diag_dir):
            continue

        try:
            worker_files = glob.glob(os.path.join(diag_dir, "Worker_*.log"))
        except Exception as e:
            log.debug("Error globbing worker logs in %s: %s", diag_dir, e)
            continue

        if not worker_files:
            continue

        try:
            worker_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        except OSError:
            pass

        for worker_file in worker_files:
            job_id = _try_extract_job_id_from_file(worker_file)
            if job_id:
                return job_id

    return None


def _get_job_id(env: MutableMapping[str, str]) -> Optional[str]:
    """Get the numeric job ID for GitHub Actions.

    Checks JOB_CHECK_RUN_ID env var first, then falls back to diagnostics files.
    """
    job_id = env.get(JOB_CHECK_RUN_ID_ENV, "").strip()
    if job_id and job_id.isdigit():
        return job_id

    diag_job_id = _try_extract_job_id_from_diag(_get_diag_dirs())
    if diag_job_id:
        return diag_job_id

    return None


def extract_github_actions(env: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Github Actions environment."""
    github_server_url = _filter_sensitive_info(env.get("GITHUB_SERVER_URL"))
    github_repository = env.get("GITHUB_REPOSITORY")
    git_commit_sha = env.get("GITHUB_SHA")
    github_run_id = env.get("GITHUB_RUN_ID")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT")

    pipeline_url = "{0}/{1}/actions/runs/{2}".format(
        github_server_url,
        github_repository,
        github_run_id,
    )

    git_commit_head_sha = None
    if "GITHUB_EVENT_PATH" in env:
        try:
            with open(env["GITHUB_EVENT_PATH"]) as f:
                github_event_data = json.load(f)
                git_commit_head_sha = github_event_data.get("pull_request", {}).get("head", {}).get("sha")
        except Exception as e:
            log.error("Failed to read or parse GITHUB_EVENT_PATH: %s", e)

    env_vars = {
        "GITHUB_SERVER_URL": github_server_url,
        "GITHUB_REPOSITORY": github_repository,
        "GITHUB_RUN_ID": github_run_id,
    }
    if run_attempt:
        env_vars["GITHUB_RUN_ATTEMPT"] = run_attempt
        pipeline_url = "{0}/attempts/{1}".format(pipeline_url, run_attempt)

    # Resolve job ID and URL
    # Priority: JOB_CHECK_RUN_ID env var > diagnostics files
    job_name = env.get("GITHUB_JOB")
    numeric_job_id = _get_job_id(env)

    tags = {
        git.BRANCH: env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF"),
        git.COMMIT_SHA: git_commit_sha,
        git.REPOSITORY_URL: "{0}/{1}.git".format(github_server_url, github_repository),
        git.COMMIT_HEAD_SHA: git_commit_head_sha,
        _PIPELINE_ID: github_run_id,
        _PIPELINE_NAME: env.get("GITHUB_WORKFLOW"),
        _PIPELINE_NUMBER: env.get("GITHUB_RUN_NUMBER"),
        _PIPELINE_URL: pipeline_url,
        _JOB_NAME: job_name,
        _PROVIDER_NAME: "github",
        _WORKSPACE_PATH: env.get("GITHUB_WORKSPACE"),
        _CI_ENV_VARS: json.dumps(env_vars, separators=(",", ":")),
    }

    if numeric_job_id and github_run_id:
        # Use numeric job ID to build direct job URL
        tags[_JOB_ID] = numeric_job_id
        tags[_JOB_URL] = "{0}/{1}/actions/runs/{2}/job/{3}".format(
            github_server_url, github_repository, github_run_id, numeric_job_id
        )
        log.debug("GitHub Actions job URL with numeric job ID: %s", tags[_JOB_URL])
    else:
        # Fallback: use commit-based URL and omit ci.job.id
        tags[_JOB_URL] = "{0}/{1}/commit/{2}/checks".format(github_server_url, github_repository, git_commit_sha)

    return tags
