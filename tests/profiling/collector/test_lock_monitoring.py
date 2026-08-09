"""Tests for the opt-in sys.monitoring lock profiling spike."""

import sys
import threading as th
from unittest import mock

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring lock spike requires 3.12+")


@pytest.fixture
def enable_lock_sys_monitoring():
    with mock.patch(
        "ddtrace.internal.settings.profiling.config.lock.use_sys_monitoring",
        True,
    ):
        yield


def test_lock_monitoring_preserves_native_identity(enable_lock_sys_monitoring):
    from ddtrace.profiling.collector import lock_monitoring
    from ddtrace.profiling.collector._lock import _ProfiledLock
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    lock_monitoring.LockMonitoringService._instance = None

    collector = ThreadingLockCollector(capture_pct=100)
    collector.start()
    try:
        lock = th.Lock()
        assert isinstance(lock, lock_monitoring._THREAD_LOCK_TYPES)
        assert not isinstance(lock, _ProfiledLock)
        lock.acquire()
        lock.release()
    finally:
        collector.stop()


def test_lock_monitoring_service_refcount(enable_lock_sys_monitoring):
    from ddtrace.profiling.collector import lock_monitoring
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    lock_monitoring.LockMonitoringService._instance = None
    c1 = ThreadingLockCollector(capture_pct=0)
    c2 = ThreadingLockCollector(capture_pct=0)
    c1.start()
    c2.start()
    assert lock_monitoring.LockMonitoringService._instance is not None
    assert lock_monitoring.LockMonitoringService._instance._refcount == 2
    c1.stop()
    assert lock_monitoring.LockMonitoringService._instance._refcount == 1
    c2.stop()
    assert lock_monitoring.LockMonitoringService._instance is None


def test_lock_monitoring_ff_off_uses_wrappers():
    """Default / FF off must keep the regular _ProfiledLock allocator wrappers."""
    from ddtrace.profiling.collector import lock_monitoring
    from ddtrace.profiling.collector._lock import _ProfiledLock
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    with mock.patch(
        "ddtrace.internal.settings.profiling.config.lock.use_sys_monitoring",
        False,
    ):
        lock_monitoring.LockMonitoringService._instance = None
        collector = ThreadingLockCollector(capture_pct=100)
        collector.start()
        try:
            lock = th.Lock()
            assert isinstance(lock, _ProfiledLock)
            assert lock_monitoring.LockMonitoringService._instance is None
        finally:
            collector.stop()
