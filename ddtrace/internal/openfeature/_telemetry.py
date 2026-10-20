"""Versioned internal boundary for Lambda's best-effort EVP flush.

Importing this module does not import OpenFeature or create any writers.
"""

import math
import time
from typing import Protocol
from weakref import WeakSet

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import PeriodicThread


FLUSH_API_VERSION = 1
log = get_logger(__name__)


class Progress:
    """Process-local delivery watermarks, independent of network/aggregation locks."""

    def __init__(self) -> None:
        self.lock = forksafe.Lock()
        self.accepted = 0
        self.settled = 0
        self.failures = 0
        self.last_failure = float("-inf")

    def accept(self) -> None:
        with self.lock:
            self.accepted += 1

    def finish(self, target: int, success: bool) -> None:
        with self.lock:
            self.settled = target
            if not success:
                self.failures += 1
                self.last_failure = time.monotonic()


class Writer(Protocol):
    _progress: Progress

    def periodic(self) -> None: ...


_writers: WeakSet[Writer] = WeakSet()
_registry_lock = forksafe.Lock()


def register(writer: Writer) -> None:
    with _registry_lock:
        _writers.add(writer)


class _Coordinator:
    def __init__(self) -> None:
        self.lock = forksafe.Lock()
        self.requested: set[Writer] = set()
        self.signal = forksafe.Event()
        self.worker = None

    def run(self) -> None:
        # A bounded idle wait lets the native fork protocol stop this worker.
        if not self.signal.wait(0.1):
            return
        with self.lock:
            writers = self.requested
            self.requested = set()
            self.signal.clear()
        for writer in writers:
            try:
                writer.periodic()
            except Exception:
                log.debug("Feature flag telemetry flush failed", exc_info=True)


_coordinator = forksafe.ResetObject(_Coordinator)


def flush(timeout: float) -> str:
    """Wait at most timeout seconds for pending EVP work across live writers.

    Returns completed, failed, or timed_out. Timeout never cancels or retries a
    send: queued and in-flight work remains owned by the existing writers and
    the single coordinator. Repeated requests coalesce by writer. Endpoint
    acceptance is not a guarantee of UI visibility or durable delivery.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        return "timed_out"
    started = time.monotonic()
    deadline = started + timeout
    if not _registry_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        return "timed_out"
    try:
        writers = list(_writers)
    finally:
        _registry_lock.release()

    pending = []
    failed = False
    for writer in writers:
        if time.monotonic() >= deadline:
            return "timed_out"
        progress = writer._progress
        if not progress.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return "timed_out"
        try:
            failed |= progress.last_failure >= started
            if progress.accepted > progress.settled:
                pending.append((writer, progress.accepted, progress.failures))
        finally:
            progress.lock.release()
    if time.monotonic() >= deadline:
        return "timed_out"
    if not pending:
        return "failed" if failed else "completed"

    coordinator = _coordinator
    if not coordinator.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        return "timed_out"
    try:
        coordinator.requested.update(writer for writer, _, _ in pending)
        if coordinator.worker is None:
            coordinator.worker = PeriodicThread(
                0.0, coordinator.run, name="ddtrace:feature-flag-flush", no_wait_at_start=True
            )
            # ResetObject replaces the coordinator in a child; don't restart its
            # inherited worker against the parent's requested work.
            coordinator.worker.__autorestart__ = False
            try:
                coordinator.worker.start()
            except Exception:
                coordinator.worker = None
                raise
        coordinator.signal.set()
    finally:
        coordinator.lock.release()

    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timed_out"
        waiting = []
        for writer, target, failures in pending:
            progress = writer._progress
            if not progress.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                return "timed_out"
            try:
                failed |= progress.failures != failures
                if progress.settled < target:
                    waiting.append((writer, target, failures))
            finally:
                progress.lock.release()
        pending = waiting
        if pending:
            time.sleep(min(0.001, max(0.0, deadline - time.monotonic())))
    return "failed" if failed else "completed"
