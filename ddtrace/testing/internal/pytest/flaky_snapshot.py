"""
Entry snapshots for tests flagged as flaky in the known-tests (libraries-tests) API.

When ``TestProperties.quarantined`` is true for a test, we register a Dynamic
Instrumentation snapshot probe on the test function for each run and capture
the snapshot UUID so it can be correlated with the test span.

Correlation contract (matches JS tracer and Exception Replay implementations):
- The DI snapshot already embeds ``dd.trace_id`` / ``dd.span_id`` from the
  active trace, linking snapshot → test span.
- The test span receives the following tags when a snapshot is captured,
  linking test span → snapshot:
    ``error.debug_info_captured``       "true"
    ``_dd.debug.error.0.snapshot_id``   snapshot UUID
    ``_dd.debug.error.0.file``          absolute path to the test file
    ``_dd.debug.error.0.line``          first line of the test function (string)
  Index 0 is reserved for test-entry snapshots; Exception Replay uses 1+.

The ``known_flaky_probe_context`` context manager owns the full lifecycle:
  1. Wrap ``collector.push`` *before* registering the probe so any signal
     emitted synchronously on registration is already intercepted.
  2. Register the probe via ``Debugger._on_configuration``.
  3. Yield a result dict that will be populated once the probe fires.
  4. On exit: restore the collector wrapper, then unregister the probe.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
import re
import typing as t

import pytest

from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import DEFAULT_PROBE_CONDITION_ERROR_RATE
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.internal.utils.inspection import undecorated
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.test_data import TestRef


log = logging.getLogger(__name__)

# Probe IDs must match [A-Za-z0-9_-]+ per registry; nodeids contain characters we strip.
_PROBE_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class KnownFlakyProbeResult(t.TypedDict, total=False):
    """Populated by ``known_flaky_probe_context`` once the probe fires."""

    snapshot_id: str  # snapshot UUID; absent if the probe did not fire
    file: str  # absolute path to the test file
    line: str  # first line of the test function as a string


def is_known_flaky_test(manager: SessionManager, test_ref: TestRef) -> bool:
    """True if test management marks this test as quarantined."""
    if not manager.settings.test_management.enabled:
        return False
    props = manager.test_properties.get(test_ref)
    return props is not None and props.quarantined


def _resolve_test_function(item: pytest.Item) -> t.Any:
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


def _probe_id_for_run(item: pytest.Item, run_serial: int) -> str:
    raw = f"dd-ci-known-flaky-{item.nodeid}-{run_serial}"
    return _PROBE_ID_SAFE.sub("_", raw)


def _build_entry_probe(item: pytest.Item, run_serial: int) -> t.Optional[tuple[LogFunctionProbe, str, str]]:
    """
    Build a LogFunctionProbe that fires at the entry of the test function.

    Returns ``(probe, file_path, line_number)`` or ``None`` if the test
    function cannot be resolved.
    """
    func = _resolve_test_function(item)
    if func is None:
        return None

    file_path = getattr(func.__code__, "co_filename", None) or ""
    line_number = str(getattr(func.__code__, "co_firstlineno", 0))

    probe = LogFunctionProbe(
        probe_id=_probe_id_for_run(item, run_serial),
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
    return probe, file_path, line_number


@contextlib.contextmanager
def known_flaky_probe_context(
    item: pytest.Item,
    run_serial: int,
    mark_debugger_started: t.Callable[[], None],
) -> t.Generator[t.Optional[KnownFlakyProbeResult], None, None]:
    """
    Context manager for the full lifecycle of a known-flaky DI probe.

    Yields a ``KnownFlakyProbeResult`` dict that is populated with
    ``snapshot_id``, ``file``, and ``line`` once the probe fires during test
    execution, or ``None`` if the probe could not be built or DI is
    unavailable.

    The snapshot is uploaded to Datadog via the existing DI ``SignalUploader``
    (through the local agent) when ``original_push`` is called.

    Usage::

        with known_flaky_probe_context(item, serial, mark_started) as result:
            reports = _make_reports_dict(runtestprotocol(item, ...))
        if result and "snapshot_id" in result:
            test_run.tags[TestTag.KNOWN_FLAKY_SNAPSHOT_ID] = result["snapshot_id"]
            ...
    """
    built = _build_entry_probe(item, run_serial)
    if built is None:
        log.debug("Could not build known-flaky probe for %s", item.nodeid)
        yield None
        return

    probe, file_path, line_number = built

    try:
        if Debugger._instance is None:
            Debugger.enable()
            mark_debugger_started()
        collector = SignalUploader.get_collector()
        if collector is None:
            log.warning(
                "Dynamic Instrumentation collector unavailable; known-flaky snapshot not registered for %s",
                item.nodeid,
            )
            yield None
            return
        dbg = Debugger._instance
        if dbg is None:
            yield None
            return
    except Exception:
        log.exception("Failed to enable DI for known-flaky probe for %s", item.nodeid)
        yield None
        return

    # Step 1: wrap collector.push BEFORE registering the probe so that any
    # signal emitted synchronously on registration is already captured.
    result: KnownFlakyProbeResult = {}
    original_push = collector.push

    def _capturing_push(signal: t.Any) -> None:
        if "snapshot_id" not in result and getattr(getattr(signal, "probe", None), "probe_id", None) == probe.probe_id:
            result["snapshot_id"] = signal.uuid
            result["file"] = file_path
            result["line"] = line_number
        return original_push(signal)

    object.__setattr__(collector, "push", _capturing_push)

    # Step 2: register the probe.
    try:
        dbg._on_configuration(ProbePollerEvent.NEW_PROBES, [probe])
    except Exception:
        log.exception("Failed to register known-flaky probe for %s", item.nodeid)
        object.__setattr__(collector, "push", original_push)
        yield None
        return

    try:
        yield result
    finally:
        # Step 4: restore collector.
        object.__setattr__(collector, "push", original_push)
        # Step 5: unregister probe.
        try:
            dbg._on_configuration(ProbePollerEvent.DELETED_PROBES, [probe])
        except Exception:
            log.debug("Failed to unregister known-flaky probe %s", probe.probe_id, exc_info=True)


def maybe_disable_debugger_started_for_known_flaky(debugger_started_here: bool) -> None:
    if not debugger_started_here:
        return
    try:
        Debugger.disable()
    except Exception:
        log.debug("Debugger.disable after known-flaky probes", exc_info=True)
