"""Shared fixtures and oracles for fault-injection tests."""

import logging
import typing

import pytest

from ddtrace.profiling import profiler
from ddtrace.internal.settings.profiling import config as profiling_config


# Watchdog deadlines. Faults must not turn into hangs: if a lifecycle call
# blocks longer than this, the profiler has wedged the host application.
START_DEADLINE = 20.0
STOP_DEADLINE = 20.0

# Make uploads frequent so run-path faults (snapshot/upload) actually fire
# during a short test.
FAST_INTERVAL = 0.2


@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    caplog.set_level(logging.CRITICAL)
    yield


@pytest.fixture(autouse=True)
def _reset_profiler_state() -> typing.Generator[None, None, None]:
    """Reset profiler globals between tests (mirrors tests/profiling/test_profiler.py)."""
    yield
    profiler.Profiler._active_instance = None
    profiler.Profiler._exit_signal_handler = None


@pytest.fixture
def fast_upload_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a fast scheduler interval so flush-path faults fire quickly.

    Env vars cannot be used here (the config is parsed at ddtrace import
    time), so we patch the settings object the Scheduler reads at init.
    """
    monkeypatch.setattr(profiling_config, "upload_interval", FAST_INTERVAL, raising=False)


def make_profiler(**kwargs: typing.Any) -> profiler.Profiler:
    """Build a profiler with a wide collector surface and fast uploads."""
    kwargs.setdefault("_memory_collector_enabled", True)
    kwargs.setdefault("_stack_collector_enabled", True)
    kwargs.setdefault("_lock_collector_enabled", True)
    kwargs.setdefault("_exception_profiling_enabled", True)
    kwargs.setdefault("_pytorch_collector_enabled", False)
    p = profiler.Profiler(**kwargs)
    # __getattr__ forwards to _ProfilerInstance; Scheduler was built in
    # __post_init__ with the (patched) interval.
    assert p._scheduler is not None
    p._scheduler.interval = FAST_INTERVAL
    return p


def assert_clean_shutdown(p: profiler.Profiler, timeout: float = STOP_DEADLINE) -> None:
    """Oracle: stop completes, no leftover profiler threads, globals cleared.

    Findings are split per product: leftover ``ddtrace.profiling.*`` workers
    fail this oracle (profiler leak); leftovers owned by other products
    (telemetry, remote-config, ...) are recorded in the failure message only —
    see test_lifecycle_faults.py::test_telemetry_threads_outlive_profiler for
    the deliberate telemetry finding.
    """
    from tests.profiling.fault_injection import faultlib

    faultlib.completes_within(lambda: p.stop(), timeout, label="Profiler.stop")

    by_product = faultlib.leftover_threads_by_product()
    profiling_leftovers = by_product.get("profiling", [])
    other_findings = {k: v for k, v in by_product.items() if k != "profiling"}
    msg = f"profiler threads leaked after stop: {[str(getattr(t, 'name', t)) for t in profiling_leftovers]}"
    if other_findings:
        msg += f" | other-product threads still running (recorded, not failing): {faultlib.format_leftovers_by_product(other_findings)}"
    assert not profiling_leftovers, msg
    assert profiler.Profiler._active_instance is not p, "_active_instance not cleared by stop"


def assert_restartable() -> None:
    """Oracle: after a faulted run, a fresh profiler still start/stops cleanly."""
    from tests.profiling.fault_injection import faultlib

    p = make_profiler()
    try:
        faultlib.completes_within(p.start, START_DEADLINE, label="restart Profiler.start")
    finally:
        faultlib.completes_within(lambda: p.stop(), STOP_DEADLINE, label="restart Profiler.stop")
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, f"profiler threads leaked after restart: {[getattr(t, 'name', t) for t in leftovers]}"
