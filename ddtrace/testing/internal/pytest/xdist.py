"""Shared pytest-xdist command-line helpers for Test Optimization plugins."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import time
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.constants import DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_MANIFEST_FILE
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.manifest import manifest_file_path
from ddtrace.testing.internal.manifest import write_manifest_cache
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


log = get_logger(__name__)

XDIST_UNSET = "UNSET"
XDIST_AUTO = "auto"
XDIST_LOGICAL = "logical"
XDIST_MANIFEST_DIRNAME = "dd-trace-py.testoptimization"
XDIST_WORKER_MANIFEST_WAIT_SECONDS = 5.0
XDIST_WORKER_MANIFEST_WAIT_INTERVAL_SECONDS = 0.05


def xdist_manifest_dir(workspace_path: t.Optional[Path]) -> t.Optional[Path]:
    if workspace_path is None:
        return None
    git_path = workspace_path / ".git"
    if git_path.is_dir():
        return git_path / XDIST_MANIFEST_DIRNAME
    return workspace_path / ("." + XDIST_MANIFEST_DIRNAME)


def xdist_manifest_path(workspace_path: t.Optional[Path]) -> t.Optional[Path]:
    manifest_dir = xdist_manifest_dir(workspace_path)
    if manifest_dir is None:
        return None
    return manifest_file_path(manifest_dir)


def wait_for_xdist_worker_manifest(workspace_path: t.Optional[Path]) -> t.Optional[Path]:
    """Ensure an xdist worker waits until the controller-generated manifest is ready.

    The controller exports DD_TEST_OPTIMIZATION_MANIFEST_FILE before writing the manifest so workers spawned early
    inherit manifest mode. Since manifest.txt is written last, its existence is the cache-readiness signal.
    """
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return None

    manifest_env = os.environ.get(DD_TEST_OPTIMIZATION_MANIFEST_FILE)
    manifest_path = Path(manifest_env) if manifest_env else xdist_manifest_path(workspace_path)
    if manifest_path is None or XDIST_MANIFEST_DIRNAME not in manifest_path.parts:
        return None

    deadline = time.time() + XDIST_WORKER_MANIFEST_WAIT_SECONDS
    while time.time() < deadline:
        if manifest_path.exists():
            os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] = str(manifest_path)
            return manifest_path
        time.sleep(XDIST_WORKER_MANIFEST_WAIT_INTERVAL_SECONDS)
    return None


def export_git_metadata_for_workers(env_tags: t.Mapping[str, t.Optional[str]]) -> bool:
    """Export controller-only git metadata through env so xdist workers can read it at startup."""
    merge_base = env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA)
    if merge_base and DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA not in os.environ:
        os.environ[DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA] = merge_base
        return True
    return False


def cleanup_exported_git_metadata(env_tags: t.Mapping[str, t.Optional[str]]) -> None:
    merge_base = env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA)
    if merge_base and os.environ.get(DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA) == merge_base:
        os.environ.pop(DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA, None)


def cleanup_xdist_manifest_artifacts(workspace_path: t.Optional[Path]) -> None:
    manifest_dir = xdist_manifest_dir(workspace_path)
    if manifest_dir is None:
        return
    try:
        shutil.rmtree(manifest_dir, ignore_errors=True)
    except OSError as e:
        log.debug("Could not remove xdist manifest dir %s: %s", manifest_dir, e)


def write_xdist_manifest_cache(
    workspace_path: t.Optional[Path],
    settings: Settings,
    known_tests: set[TestRef],
    test_properties: dict[TestRef, TestProperties],
    skippable_items: set[t.Union[SuiteRef, TestRef]],
    itr_correlation_id: t.Optional[str],
) -> t.Optional[Path]:
    """Write a manifest-mode cache for pytest-xdist workers and return its manifest path."""
    manifest_dir = xdist_manifest_dir(workspace_path)
    if manifest_dir is None:
        return None
    try:
        return write_manifest_cache(
            manifest_dir,
            settings,
            known_tests,
            test_properties,
            skippable_items,
            itr_correlation_id,
        )
    except (OSError, TypeError) as e:
        log.debug("Could not write xdist manifest cache %s: %s", manifest_dir, e)
        return None


def parse_worker_value(val: str) -> t.Union[int, str]:
    """Parse a pytest-xdist worker-count value."""
    if val in (XDIST_AUTO, XDIST_LOGICAL):
        return val
    try:
        return int(val)
    except ValueError:
        return XDIST_UNSET


def parse_xdist_args_from_cmd(args: list[str]) -> tuple[t.Union[int, str], str]:
    """Parse pytest-xdist worker-count and distribution mode arguments."""
    num_workers: t.Union[int, str] = XDIST_UNSET
    dist_mode = XDIST_UNSET

    def set_workers_and_dist(val: str) -> None:
        nonlocal num_workers, dist_mode
        num_workers = parse_worker_value(val)
        if (
            num_workers
            and (isinstance(num_workers, int) or num_workers in (XDIST_AUTO, XDIST_LOGICAL))
            and dist_mode == XDIST_UNSET
        ):
            dist_mode = "load"

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "-n" and i + 1 < len(args):
            set_workers_and_dist(args[i + 1])
            i += 1
        elif arg.startswith("-n") and len(arg) > 2:
            set_workers_and_dist(arg[2:])
        elif arg == "--numprocesses" and i + 1 < len(args):
            set_workers_and_dist(args[i + 1])
            i += 1
        elif arg.startswith("--numprocesses="):
            set_workers_and_dist(arg.split("=", 1)[1])
        elif arg == "--dist" and i + 1 < len(args):
            dist_mode = args[i + 1]
            i += 1
        elif arg.startswith("--dist="):
            dist_mode = arg.split("=", 1)[1]

        i += 1

    return num_workers, dist_mode


def is_xdist_enabled_from_args(args: list[str]) -> bool:
    """Return whether command-line args or the live environment indicate xdist is active."""
    num_workers, _ = parse_xdist_args_from_cmd(args)
    return num_workers not in (XDIST_UNSET, 0, None) or bool(os.environ.get("PYTEST_XDIST_WORKER"))
