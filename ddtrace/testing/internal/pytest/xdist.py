"""Shared pytest-xdist command-line helpers for Test Optimization plugins."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.constants import DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_MANIFEST_FILE
from ddtrace.testing.internal.constants import XDIST_MANIFEST_DIR_PREFIX
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.manifest import write_manifest_cache
from ddtrace.testing.internal.offline_mode import get_offline_mode
from ddtrace.testing.internal.offline_mode import reset_offline_mode


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.session_manager import SessionManager


log = get_logger(__name__)

XDIST_UNSET = "UNSET"
XDIST_AUTO = "auto"
XDIST_LOGICAL = "logical"

# AIDEV-NOTE: The controller-generated manifest cache lives in a private temp directory (XDIST_MANIFEST_DIR_PREFIX)
# instead of the workspace: the workspace path is derived differently by different components (git root vs. CI provider
# env vars), and two concurrent pytest runs of the same repo would otherwise clobber each other's cache.  The controller
# pid is embedded in the directory name so descendant processes can tell whether an inherited manifest was meant for
# them, and so OfflineMode can tell a generated manifest from an externally provided (Bazel) one.


def is_xdist_worker_process() -> bool:
    """Return whether this process is a pytest-xdist worker (the env var is set before plugins load)."""
    return bool(os.environ.get("PYTEST_XDIST_WORKER"))


class XdistManifest(t.NamedTuple):
    """Bookkeeping for the manifest cache an xdist controller generated for its workers."""

    path: Path
    git_metadata_exported: bool


def generate_xdist_manifest(session_manager: SessionManager, args: list[str]) -> t.Optional[XdistManifest]:
    """Publish the controller's backend data as a manifest cache for the xdist workers it is about to spawn.

    Without this, every worker would query settings, known tests, test management and skippable tests again.  Workers
    are spawned after the plugin's early hooks run, so exporting the manifest env var once the cache is fully written
    is all they need to inherit manifest mode -- no waiting or retrying on the worker side.

    Returns the bookkeeping needed by ``cleanup_xdist_manifest``, or None when no manifest is needed: xdist is not in
    use, this process is a worker (its controller already generated one), or a user-provided (Bazel) manifest is
    already in effect and workers inherit it as-is.
    """
    if not is_xdist_enabled_from_args(args) or is_xdist_worker_process() or get_offline_mode().manifest_enabled:
        return None

    if session_manager.configuration_errors:
        # Some of what we fetched is default or partial data. Publishing it would hand the same degraded state to every
        # worker; leaving them online means each one retries on its own, as it did before manifest generation existed.
        log.debug(
            "Not caching backend data for xdist workers: this session has configuration errors (%s)",
            sorted(session_manager.configuration_errors),
        )
        return None

    # This is only an optimization: nothing that happens here may break the test session.  A read-only or full
    # TMPDIR, for instance, just means the workers query the backend the way they did before.
    manifest_dir = None
    try:
        manifest_dir = Path(tempfile.mkdtemp(prefix=f"{XDIST_MANIFEST_DIR_PREFIX}{os.getpid()}_"))
        manifest_path = write_manifest_cache(
            manifest_dir,
            session_manager.settings,
            session_manager.known_tests,
            session_manager.test_properties,
            session_manager.skippable_items,
            session_manager.itr_correlation_id,
        )
    except Exception as e:
        log.debug("Could not write xdist manifest cache %s: %s", manifest_dir, e)
        if manifest_dir is not None:
            shutil.rmtree(manifest_dir, ignore_errors=True)
        return None

    os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] = str(manifest_path)
    git_metadata_exported = _export_git_metadata_for_workers(session_manager.env_tags)
    log.info("Test Optimization cached backend data for xdist workers in %s", manifest_path)
    log.debug("Test Optimization exported git metadata for xdist workers: %s", git_metadata_exported)
    return XdistManifest(path=manifest_path, git_metadata_exported=git_metadata_exported)


def cleanup_xdist_manifest(manifest: XdistManifest, env_tags: t.Mapping[str, t.Optional[str]]) -> None:
    """Remove the generated manifest cache and the env vars exported for the workers."""
    if os.environ.get(DD_TEST_OPTIMIZATION_MANIFEST_FILE) == str(manifest.path):
        os.environ.pop(DD_TEST_OPTIMIZATION_MANIFEST_FILE, None)
    shutil.rmtree(manifest.path.parent, ignore_errors=True)
    if manifest.git_metadata_exported:
        _cleanup_exported_git_metadata(env_tags)


def resolve_inherited_manifest_env() -> None:
    """Decide what to make of a ``DD_TEST_OPTIMIZATION_MANIFEST_FILE`` this process inherited from another one.

    - Not one of ours (a user-provided Bazel manifest): left untouched.
    - Generated by this process or its parent, i.e. we are the controller itself or one of the workers it spawned:
      kept, so the worker reads the cache instead of querying the backend.
    - Generated by anything else, typically a nested pytest run started from inside a worker: dropped, so the nested
      session does not read the outer session's cached backend data.

    A worker that keeps a manifest it cannot read falls back to querying the backend.  That is safe, and the spawn
    ordering makes it unreachable, but it defeats the point of generating the manifest -- so say so out loud rather
    than degrade silently.

    AIDEV-NOTE: Known limitation. A nested pytest session started *in-process* from inside a worker
    (``pytest.main()``, ``pytester.inline_run()``) shares the worker's pid, so it looks like the manifest's rightful
    owner and reads the outer session's cache. Dropping the env var would not help either: ``OfflineMode`` is a
    process-wide singleton that the worker already resolved. Fixing it properly means scoping offline mode to a
    session rather than a process. In practice the nested run usually has the plugin disabled, and ``tests/testing``
    clears the variable for the in-process cases that do enable it.
    """
    manifest_env = os.environ.get(DD_TEST_OPTIMIZATION_MANIFEST_FILE)
    if not manifest_env:
        return
    owner_pid = _generated_manifest_owner_pid(manifest_env)
    if owner_pid is None:
        return

    if owner_pid not in (os.getpid(), os.getppid()):
        log.debug(
            "Ignoring Test Optimization manifest generated by an unrelated session: path=%s owner_pid=%s ppid=%s",
            manifest_env,
            owner_pid,
            os.getppid(),
        )
        os.environ.pop(DD_TEST_OPTIMIZATION_MANIFEST_FILE, None)
        reset_offline_mode()
        return

    if not is_xdist_worker_process():
        return

    # One line per worker, at INFO, so it is visible at a glance whether the workers really are reading the cache
    # instead of querying the backend.
    if get_offline_mode().manifest_enabled:
        log.info(
            "Test Optimization xdist worker %s is reading backend data cached by its controller in %s",
            os.environ.get("PYTEST_XDIST_WORKER"),
            manifest_env,
        )
    else:
        log.warning(
            "Test Optimization xdist worker %s could not read the manifest generated by its controller (%s) and will "
            "query the backend instead",
            os.environ.get("PYTEST_XDIST_WORKER"),
            manifest_env,
        )


def _generated_manifest_owner_pid(manifest_path: str) -> t.Optional[int]:
    """Return the pid of the controller that generated ``manifest_path``, or None if we did not generate it."""
    dir_name = Path(manifest_path).parent.name
    if not dir_name.startswith(XDIST_MANIFEST_DIR_PREFIX):
        return None
    pid_part = dir_name[len(XDIST_MANIFEST_DIR_PREFIX) :].split("_", 1)[0]
    try:
        return int(pid_part)
    except ValueError:
        return None


def _export_git_metadata_for_workers(env_tags: t.Mapping[str, t.Optional[str]]) -> bool:
    """Export controller-only git metadata through env so xdist workers can read it at startup."""
    merge_base = env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA)
    if merge_base and DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA not in os.environ:
        os.environ[DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA] = merge_base
        return True
    return False


def _cleanup_exported_git_metadata(env_tags: t.Mapping[str, t.Optional[str]]) -> None:
    merge_base = env_tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA)
    if merge_base and os.environ.get(DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA) == merge_base:
        os.environ.pop(DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA, None)


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
