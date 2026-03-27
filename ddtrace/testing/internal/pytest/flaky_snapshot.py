"""
DI entry snapshot for quarantined tests.

For each run of a quarantined test we register a LogFunctionProbe at the test
function entry. When the probe fires it emits a snapshot; we intercept
collector.push to capture the UUID before it is encoded.

Correlation contract:
- The snapshot carries dd.trace_id/dd.span_id → links snapshot to test span.
- The test span receives error.debug_info_captured and _dd.debug.error.0.*
  tags → links test span to snapshot (index 0; Exception Replay uses 1+).
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import re
import typing as t

import pytest

from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import DEFAULT_PROBE_CONDITION_ERROR_RATE
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.inspection import undecorated
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.test_data import TestRef


log = logging.getLogger(__name__)

# Feature flag: set _DD_CIVISIBILITY_FLAKY_SNAPSHOT_ENABLED=true to enable
# DI-based entry snapshot capture for quarantined tests. Disabled by default.
FLAKY_SNAPSHOT_ENABLED = asbool(os.environ.get("_DD_CIVISIBILITY_FLAKY_SNAPSHOT_ENABLED", "false"))

# Probe IDs must match [A-Za-z0-9_-]+; node IDs contain characters we strip.
_PROBE_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def is_known_flaky_test(manager: SessionManager, test_ref: TestRef) -> bool:
    """True if test management marks this test as quarantined."""
    if not manager.settings.test_management.enabled:
        return False
    props = manager.test_properties.get(test_ref)
    return props is not None and props.quarantined


def _resolve_test_function(item: pytest.Item) -> t.Any:
    """Return the unwrapped test function, or None if it cannot be resolved."""
    if not hasattr(item, "_obj"):
        return None
    try:
        item_path = (
            Path(item.path).absolute() if getattr(item, "path", None) is not None else Path(str(item.fspath)).absolute()
        )
    except Exception:  # noqa: BLE001
        return None
    try:
        return undecorated(item._obj, item.name, item_path)
    except Exception:  # noqa: BLE001
        return getattr(item, "_obj", None)


@contextlib.contextmanager
def flaky_snapshot_context(
    item: pytest.Item,
    run_serial: int,
    debugger: t.Any,
) -> t.Generator[t.Optional[dict], None, None]:
    """
    Register a DI entry probe for a quarantined test and capture the snapshot UUID.

    Yields a dict with snapshot_id, file, line once the probe fires, or None
    if the probe could not be set up. The caller is responsible for enabling
    the Debugger before calling this and disabling it at session end.
    """
    func = _resolve_test_function(item)
    if func is None:
        log.debug("Could not resolve test function for %s", item.nodeid)
        yield None
        return

    collector = SignalUploader.get_collector()
    if collector is None:
        log.warning("DI collector unavailable for flaky snapshot on %s", item.nodeid)
        yield None
        return

    file_path = getattr(func.__code__, "co_filename", None) or ""
    line_number = str(getattr(func.__code__, "co_firstlineno", 0))
    probe_id = _PROBE_ID_SAFE.sub("_", f"dd-ci-flaky-{item.nodeid}-{run_serial}")

    probe = LogFunctionProbe(
        probe_id=probe_id,
        version=0,
        tags={},
        module=func.__module__,
        func_qname=func.__qualname__,
        evaluate_at=ProbeEvalTiming.ENTRY,
        template="",
        segments=[],
        take_snapshot=True,
        capture_expressions=[],
        limits=DEFAULT_CAPTURE_LIMITS,
        condition=None,
        condition_error_rate=DEFAULT_PROBE_CONDITION_ERROR_RATE,
        rate=float("inf"),
    )

    # Wrap collector.push BEFORE registering the probe so any signal emitted
    # synchronously on registration is already intercepted.
    result: dict = {}
    original_push = collector.push

    def _capturing_push(signal: t.Any) -> None:
        if not result and getattr(getattr(signal, "probe", None), "probe_id", None) == probe_id:
            result["snapshot_id"] = signal.uuid
            result["file"] = file_path
            result["line"] = line_number
        return original_push(signal)

    setattr(collector, "push", _capturing_push)
    try:
        debugger._on_configuration(ProbePollerEvent.NEW_PROBES, [probe])
    except Exception:
        log.exception("Failed to register flaky snapshot probe for %s", item.nodeid)
        setattr(collector, "push", original_push)
        yield None
        return

    try:
        yield result
    finally:
        setattr(collector, "push", original_push)
        try:
            debugger._on_configuration(ProbePollerEvent.DELETED_PROBES, [probe])
        except Exception:
            log.debug("Failed to unregister flaky snapshot probe %s", probe_id, exc_info=True)
