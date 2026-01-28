"""
GitHub Actions CI provider integration.

This module contains all GitHub Actions-specific logic for extracting CI metadata,
including job ID resolution from environment variables and diagnostics files.

GitHub Actions Job ID Resolution:
    The numeric job ID (check_run_id) is needed to construct proper ci.job.url links.
    This module attempts to resolve it using the following priority order:

    1. JOB_CHECK_RUN_ID environment variable (highest priority)
       - This should be explicitly set by users or GitHub Actions setup
       - Must contain a numeric value (validated by is_numeric_job_id)

    2. GitHub Actions diagnostics files (fallback)
       - Reads from Worker_*.log files in platform-specific _diag directories
       - Parses JSON or uses regex to extract check_run_id
       - Newest files are checked first (sorted by modification time)

    If neither method yields a numeric job ID, None is returned and the function
    falls back to using the GITHUB_JOB environment variable (job name).
"""

import glob
import json
import os
import platform
import re
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import MutableMapping  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


# GitHub Actions job ID resolution constants
JOB_CHECK_RUN_ID_ENV = "JOB_CHECK_RUN_ID"
MAX_DIAG_FILE_SIZE = 10 * 1024 * 1024  # 10MB
DIAG_ENABLED = True  # Can be disabled in tests

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


def _is_numeric_job_id(job_id):
    # type: (str) -> bool
    """Validate that the job ID contains only digits."""
    if not job_id:
        return False
    return job_id.isdigit()


def _get_diag_dirs():
    # type: () -> List[str]
    """Get OS-specific diagnostics directory paths."""
    system = platform.system()
    if system == "Windows":
        candidates = []
        # Try to get ProgramFiles paths from environment
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
        # Fallback hardcoded paths
        candidates.extend(
            [
                r"C:\actions-runner\cached\_diag",
                r"C:\actions-runner\_diag",
            ]
        )
        # Deduplicate and filter empty strings
        seen = set()
        result = []
        for path in candidates:
            if path and path not in seen:
                seen.add(path)
                result.append(path)
        return result
    elif system == "Darwin":
        return DIAG_DIRS_DARWIN
    else:
        return DIAG_DIRS_LINUX


def _try_extract_job_id_from_json(content):
    # type: (str) -> Optional[str]
    """Attempt to parse content as JSON and extract check_run_id."""
    try:
        data = json.loads(content)
        job_data = data.get("job", {}).get("d", [])
        for item in job_data:
            if item.get("k") == "check_run_id":
                value = item.get("v")
                if isinstance(value, (int, float)):
                    # Reject non-integer floats
                    if isinstance(value, float) and value != int(value):
                        continue
                    job_id = str(int(value))
                elif isinstance(value, str):
                    job_id = value.strip()
                else:
                    continue
                if _is_numeric_job_id(job_id):
                    return job_id
    except (ValueError, KeyError, TypeError):
        pass
    return None


def _try_extract_job_id_from_regex(content):
    # type: (str) -> Optional[str]
    """Attempt to extract check_run_id using regex."""
    match = CHECK_RUN_ID_REGEX.search(content)
    if match:
        job_id = match.group(1).strip()
        if _is_numeric_job_id(job_id):
            return job_id
    return None


def _try_extract_job_id_from_file(file_path):
    # type: (str) -> Optional[str]
    """Extract job ID from a single Worker log file."""
    try:
        # Check file size
        file_stat = os.stat(file_path)
        if file_stat.st_size > MAX_DIAG_FILE_SIZE:
            log.debug("Skipping oversized diagnostics file %s (%d bytes)", file_path, file_stat.st_size)
            return None

        # Read file content
        with open(file_path, "r") as f:
            content = f.read()

        # Try JSON parsing first
        job_id = _try_extract_job_id_from_json(content)
        if job_id:
            log.debug("Extracted github actions job id via JSON: %s from %s", job_id, file_path)
            return job_id

        # Fall back to regex extraction
        job_id = _try_extract_job_id_from_regex(content)
        if job_id:
            log.debug("Extracted github actions job id via regex: %s from %s", job_id, file_path)
            return job_id

    except (OSError, IOError) as e:
        log.debug("Error reading file %s: %s", file_path, e)

    return None


def _try_extract_job_id_from_diag(diag_dirs):
    # type: (List[str]) -> Optional[str]
    """Attempt to extract job ID from GitHub Actions diagnostics files."""
    for diag_dir in diag_dirs:
        # Check if directory exists
        if not os.path.isdir(diag_dir):
            continue

        # Find Worker_*.log files
        try:
            worker_files = glob.glob(os.path.join(diag_dir, "Worker_*.log"))
        except Exception as e:
            log.debug("Error globbing worker logs in %s: %s", diag_dir, e)
            continue

        if not worker_files:
            continue

        # Sort by modification time (newest first)
        try:
            worker_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        except OSError:
            pass

        # Try to extract job ID from each file
        for worker_file in worker_files:
            job_id = _try_extract_job_id_from_file(worker_file)
            if job_id:
                return job_id

    return None


def _get_job_id(env):
    # type: (MutableMapping[str, str]) -> Optional[str]
    """
    Get the numeric job ID for GitHub Actions.

    First checks JOB_CHECK_RUN_ID environment variable, then falls back
    to reading from GitHub Actions diagnostics files.
    Only returns valid numeric job IDs.

    Args:
        env: Environment variables mapping

    Returns:
        Numeric job ID string if found, None otherwise
    """
    # Priority 1: Environment variable (only if numeric)
    job_id = env.get(JOB_CHECK_RUN_ID_ENV, "").strip()
    if job_id and _is_numeric_job_id(job_id):
        return job_id

    # Priority 2: Diagnostics files (can be disabled in tests)
    if DIAG_ENABLED:
        job_id = _try_extract_job_id_from_diag(_get_diag_dirs())
        if job_id:
            return job_id

    return None


def extract_github_actions(
    env,
    _ci_env_vars_tag,
    _filter_sensitive_info,
    git_module,
    job_id_tag,
    job_name_tag,
    job_url_tag,
    pipeline_id_tag,
    pipeline_name_tag,
    pipeline_number_tag,
    pipeline_url_tag,
    provider_name_tag,
    workspace_path_tag,
):
    # type: (...) -> Dict[str, Optional[str]]
    """Extract CI tags from Github Actions environment."""
    github_server_url = _filter_sensitive_info(env.get("GITHUB_SERVER_URL"))
    github_repository = env.get("GITHUB_REPOSITORY")
    git_commit_sha = env.get("GITHUB_SHA")
    github_run_id = env.get("GITHUB_RUN_ID")
    github_job_id = env.get(JOB_CHECK_RUN_ID_ENV)
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
    # Priority for job ID resolution:
    # 1. JOB_CHECK_RUN_ID environment variable (numeric job ID)
    # 2. GitHub Actions diagnostics files (Worker_*.log in _diag directories)
    # 3. Fallback to GITHUB_JOB (job name string)
    github_job_id = job_name = env.get("GITHUB_JOB")
    job_url = "{0}/{1}/commit/{2}/checks".format(github_server_url, github_repository, git_commit_sha)
    numeric_job_id = _get_job_id(env)  # Checks JOB_CHECK_RUN_ID, then diagnostics files

    if numeric_job_id and github_run_id:
        # Use numeric job ID to build direct job URL
        github_job_id = numeric_job_id
        job_url = "{0}/{1}/actions/runs/{2}/job/{3}".format(
            github_server_url, github_repository, github_run_id, numeric_job_id
        )
        log.debug("GitHub Actions job URL with numeric job ID: %s", job_url)

    return {
        git_module.BRANCH: env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF"),
        git_module.COMMIT_SHA: git_commit_sha,
        git_module.REPOSITORY_URL: "{0}/{1}.git".format(github_server_url, github_repository),
        git_module.COMMIT_HEAD_SHA: git_commit_head_sha,
        job_id_tag: github_job_id,
        job_url_tag: job_url,
        pipeline_id_tag: github_run_id,
        pipeline_name_tag: env.get("GITHUB_WORKFLOW"),
        pipeline_number_tag: env.get("GITHUB_RUN_NUMBER"),
        pipeline_url_tag: pipeline_url,
        job_name_tag: job_name,
        provider_name_tag: "github",
        workspace_path_tag: env.get("GITHUB_WORKSPACE"),
        _ci_env_vars_tag: json.dumps(env_vars, separators=(",", ":")),
    }
