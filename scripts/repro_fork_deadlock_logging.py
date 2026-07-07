#!/usr/bin/env python3
"""
Deterministically reproduce a ddtrace fork deadlock caused by logging from an
``after_in_child`` fork hook.

The important sequence is:

1. A non-fork-safe logging handler lock is held by a background thread.
2. The process forks while a ddtrace PeriodicThread is active.
3. ddtrace's child at-fork hook logs while restarting that PeriodicThread.
4. The child inherits the held handler lock but not the owner thread, so it
   blocks before returning from os.fork().

Run directly inside an environment with ddtrace installed, or use
``scripts/run-repro-fork-deadlock.sh``.
"""

import logging
import os
import signal
import sys
import threading
import time
import typing as t


class NonForkSafeHandler(logging.Handler):
    """A handler whose lock is intentionally not reset after fork.

    stdlib logging handlers are normally reinitialized after fork on modern
    Python versions. This handler models real-world/custom handlers that guard
    emit with locks unknown to logging's at-fork reset machinery.

    To isolate the bug we are chasing, this handler only blocks in the forked
    child. ddtrace also logs in ``before_fork`` in the parent, but this repro is
    specifically for ``after_in_child`` logging before ``os.fork()`` returns.
    """

    def __init__(self, external_lock: threading.Lock) -> None:
        super().__init__(level=logging.DEBUG)
        self.external_lock = external_lock
        self.parent_pid = os.getpid()

    def acquire(self) -> None:  # logging.Handler.handle() calls this before emit().
        if os.getpid() != self.parent_pid:
            self.external_lock.acquire()

    def release(self) -> None:
        if os.getpid() != self.parent_pid:
            self.external_lock.release()

    def emit(self, record: logging.LogRecord) -> None:
        stderr = sys.__stderr__
        if stderr is not None:
            stderr.write(self.format(record) + "\n")
            stderr.flush()


def _hold_lock_forever(lock: threading.Lock, ready: threading.Event) -> None:
    lock.acquire()
    ready.set()
    while True:
        time.sleep(3600)


def _install_deadlocking_logging_handler() -> None:
    external_lock = threading.Lock()
    ready = threading.Event()
    threading.Thread(target=_hold_lock_forever, args=(external_lock, ready), daemon=True).start()
    if not ready.wait(5):
        raise RuntimeError("background lock holder did not start")

    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(logging.DEBUG)
    handler = NonForkSafeHandler(external_lock)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(handler)

    # Ensure ddtrace.internal.threads debug logs reach the root handler.
    logging.getLogger("ddtrace.internal.threads").setLevel(logging.DEBUG)


def _start_ddtrace_periodic_thread() -> t.Any:
    from ddtrace.internal.threads import PeriodicThread

    def periodic() -> None:
        return None

    thread = PeriodicThread(60, periodic, name="repro-periodic-thread")
    thread.start()
    return thread


def main() -> int:
    import ddtrace  # noqa: F401
    from ddtrace.version import __version__

    print(f"ddtrace {__version__}, python {sys.version.split()[0]}, pid {os.getpid()}", flush=True)

    periodic_thread = _start_ddtrace_periodic_thread()
    _install_deadlocking_logging_handler()

    print("forking once; child should deadlock inside ddtrace's after_in_child logging", flush=True)
    child_pid = os.fork()
    if child_pid == 0:
        # If the bug is fixed, the child reaches this line and exits cleanly.
        os._exit(0)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        done_pid, status = os.waitpid(child_pid, os.WNOHANG)
        if done_pid == child_pid:
            periodic_thread.stop()
            periodic_thread.join(1)
            print(f"child exited without deadlock, status={status}", flush=True)
            return 0
        time.sleep(0.05)

    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)
    periodic_thread.stop()
    periodic_thread.join(1)
    print("REPRODUCED: child deadlocked before returning from os.fork()", flush=True)
    return 124


if __name__ == "__main__":
    raise SystemExit(main())
