"""
xdist-related functionality for pytest CI Visibility plugin.

This module contains all logic related to pytest-xdist parallelization mode detection
for ITR skipping level configuration.
"""

import os
import typing as t

import pytest

from ddtrace.contrib.internal.pytest._types import pytest_Config
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.api import InternalTestSession
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

# xdist-related constants
PYTEST_XDIST_WORKER_VALUE = os.environ.get("PYTEST_XDIST_WORKER")
XDIST_UNSET = "UNSET"
XDIST_AUTO = "auto"
XDIST_LOGICAL = "logical"


def parse_worker_value(val: str) -> t.Union[int, str]:
    """Parse worker count value, handling auto/logical or integers."""
    if val in (XDIST_AUTO, XDIST_LOGICAL):
        return val
    try:
        return int(val)
    except ValueError:
        return XDIST_UNSET


def _parse_xdist_args_from_cmd(args: t.List[str]) -> t.Tuple[t.Union[int, str], str]:
    """
    Parse xdist-related arguments from command line args.

    Returns:
        tuple: (num_workers, dist_mode)
               num_workers can be int, "auto", "logical", or None
               dist_mode can be str or None
    """

    def set_workers_and_dist(val: str) -> None:
        """Set num_workers and ensure dist_mode defaults to 'load'."""
        nonlocal num_workers, dist_mode
        num_workers = parse_worker_value(val)
        if (
            num_workers
            and (isinstance(num_workers, int) or num_workers in (XDIST_AUTO, XDIST_LOGICAL))
            and dist_mode == XDIST_UNSET
        ):
            dist_mode = "load"

    num_workers = XDIST_UNSET
    dist_mode = XDIST_UNSET

    i = 0
    while i < len(args):
        arg = args[i]

        # Handle -n and --numprocesses arguments
        if arg == "-n" and i + 1 < len(args):
            # -n <value>
            set_workers_and_dist(args[i + 1])
            i += 1
        elif arg.startswith("-n") and len(arg) > 2:
            # -n<value> (no space)
            set_workers_and_dist(arg[2:])
        elif arg == "--numprocesses" and i + 1 < len(args):
            # --numprocesses <value>
            set_workers_and_dist(args[i + 1])
            i += 1
        elif arg.startswith("--numprocesses="):
            # --numprocesses=<value>
            set_workers_and_dist(arg.split("=", 1)[1])

        # Handle --dist arguments
        elif arg == "--dist" and i + 1 < len(args):
            # --dist <mode>
            dist_mode = args[i + 1]
            i += 1
        elif arg.startswith("--dist="):
            # --dist=<mode>
            dist_mode = arg.split("=", 1)[1]

        i += 1

    return num_workers, dist_mode


def _skipping_level_for_xdist_parallelization_mode(
    config: pytest_Config,
    num_workers: t.Union[int, str, None],
    dist_mode: t.Union[str, None],
) -> ITR_SKIPPING_LEVEL:
    """
    Detect pytest-xdist parallelization mode and return the appropriate ITR skipping level.

    Priority order:
    1. If _DD_CIVISIBILITY_ITR_SUITE_MODE is explicitly set -> honor it regardless of xdist
    2. If xdist is used -> automatic detection based on distribution mode
    3. Fallback -> default to suite mode

    Returns:
        ITR_SKIPPING_LEVEL.SUITE for suite-level parallelization modes (loadscope, loadfile, loadgroup)
        ITR_SKIPPING_LEVEL.TEST for test-level parallelization modes (default, worksteal)
    """
    # Priority 1: Check if env var is explicitly set (not using default)
    explicit_suite_mode = os.getenv("_DD_CIVISIBILITY_ITR_SUITE_MODE")
    if explicit_suite_mode is not None:
        result = ITR_SKIPPING_LEVEL.SUITE if asbool(explicit_suite_mode) else ITR_SKIPPING_LEVEL.TEST
        log.debug(
            "Explicit ITR skipping level from _DD_CIVISIBILITY_ITR_SUITE_MODE=%s",
            explicit_suite_mode,
        )
        return result

    if config and not config.pluginmanager.hasplugin("xdist"):
        log.debug("xdist not available, using default ITR suite-level skipping")
        return ITR_SKIPPING_LEVEL.SUITE

    # If explicit parameters are not provided, try to read from config
    # Only fall back to config when parameters are unset (not explicitly provided)
    if num_workers == XDIST_UNSET and config:
        try:
            num_workers = getattr(config.option, "numprocesses", None)
            log.debug("DEBUG: num_workers read from config: %r", num_workers)
        except AttributeError:
            num_workers = None
    if dist_mode == XDIST_UNSET and config:
        try:
            dist_mode = getattr(config.option, "dist", None)
            log.debug("DEBUG: dist_mode read from config: %r", dist_mode)
        except AttributeError:
            dist_mode = None

    # This function now expects pre-parsed values from the caller
    # If both are None, it means no xdist configuration was detected

    # If xdist is installed but not being used, use default
    # Note: when using -n X without --dist, xdist defaults to "load" mode, but early config shows dist="no"
    # So we should check num_workers first - if it's > 0, xdist is being used regardless of dist_mode
    if num_workers in (0, None, XDIST_UNSET):
        log.debug("xdist not being used (no workers specified), using default ITR suite-level skipping")
        return ITR_SKIPPING_LEVEL.SUITE

    # xdist is being used, detect the parallelization mode
    # If dist_mode is "no" but we have workers, xdist defaults to "load" mode
    if dist_mode == "no":
        dist_mode = "load"
        log.debug("xdist being used without explicit --dist, defaulting to load mode")

    if dist_mode in ("loadscope", "loadfile"):
        # Suite-level parallelization modes - keep tests together by suite/file/group
        log.debug("Detected xdist suite-level parallelization mode (%s), using ITR suite-level skipping", dist_mode)
        return ITR_SKIPPING_LEVEL.SUITE
    else:
        # Test-level parallelization modes (load, worksteal, or default)
        log.debug("Detected xdist test-level parallelization mode (%s), using ITR test-level skipping", dist_mode)
        return ITR_SKIPPING_LEVEL.TEST


class XdistHooks:
    @pytest.hookimpl
    def pytest_configure_node(self, node):
        main_session_span = InternalTestSession.get_span()
        if main_session_span:
            root_span = main_session_span.span_id
        else:
            root_span = 0

        node.workerinput["root_span"] = root_span

    @pytest.hookimpl
    def pytest_testnodedown(self, node, error):
        if hasattr(node, "workeroutput") and "itr_skipped_count" in node.workeroutput:
            if not hasattr(pytest, "global_worker_itr_results"):
                pytest.global_worker_itr_results = 0
            pytest.global_worker_itr_results += node.workeroutput["itr_skipped_count"]
