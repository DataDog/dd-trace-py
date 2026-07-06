import logging
import os
import sys
import time
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.native import SharedRuntime
from ddtrace.version import __version__


log = logging.getLogger(__name__)

_DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000

# Minimum seconds between unconditional heartbeat lines (per process). Heartbeats
# piggyback on fork hooks, so this throttles busy forkers without hiding the trend
# (counters are cumulative).
_HEARTBEAT_INTERVAL_S = 15.0


class NativeRuntime(SharedRuntime):
    """Manages a SharedRuntime with fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. This class registers before_fork / after_fork_parent /
    after_fork_child hooks so the runtime is correctly paused and resumed
    around process forks.
    """

    _instance: Optional["NativeRuntime"] = None

    # Fork-hook diagnostics (APMSP fork investigation).
    #
    # ``before_fork`` tears the tokio runtime down in the parent so the child
    # inherits an empty slot. If it is skipped for a given fork, the child
    # inherits a *live* runtime and the native ``after_fork_child`` has to
    # abandon it (``mem::forget``). These process-wide counters let staging
    # tell us, with hard numbers, how often ``before_fork`` is skipped for a
    # fork that still runs ``after_fork_child`` (the asymmetric case).
    before_fork_calls: int = 0
    after_fork_parent_calls: int = 0
    after_fork_child_calls: int = 0
    # Number of forks where after_fork_child ran without a preceding before_fork.
    before_fork_skipped: int = 0

    # Highest native bare-fork / parent-gap values we have already logged. The
    # OS-level counters (via pthread_atfork) advance for forks that bypass
    # os.register_at_fork entirely, which the Python hooks above cannot see. We
    # only log when these grow, so a healthy process (no bypassing forks) stays
    # quiet after the startup marker.
    _last_bare_reported: int = 0
    _last_gap_reported: int = 0
    # Monotonic timestamp of the last heartbeat, to throttle emission.
    _last_heartbeat_ts: float = 0.0

    def __init__(self) -> None:
        super().__init__()
        # Set by before_fork, cleared after each fork in both parent and child.
        # Lets after_fork_child detect that no before_fork ran for this fork.
        self._before_fork_ran = False
        forksafe.register_before_fork(self.before_fork)
        forksafe.register_after_parent(self.after_fork_parent)
        forksafe.register(self.after_fork_child)
        atexit.register(self._atexit)
        # Always-on deployment marker so we can confirm the fork-hook diagnostics
        # build is actually running without exec'ing into the process. Emitted once
        # per process, when the NativeRuntime singleton is first created.
        #
        # Written directly to stderr rather than via the logging module on purpose:
        # k8s captures stderr unconditionally (so it shows regardless of the host
        # app's log level), and it stays invisible to tests that assert no WARNING
        # records are emitted during native module import (see tests/smoke_test.py).
        sys.stderr.write(
            "ddtrace fork-hook diagnostics build active (version=%s pid=%d gc_change=yes os_fork_diag=yes)\n"
            % (__version__, os.getpid())
        )
        sys.stderr.flush()
        # Emit one heartbeat immediately so the OS-fork counters are proven wired
        # in the deployed build (a healthy process otherwise only logs the marker).
        self._heartbeat("init", force=True)

    def _heartbeat(self, where: str, force: bool = False) -> None:
        """Unconditional, throttled dump of all fork counters (to stderr).

        Gives positive evidence even on a healthy run: we can see forks being
        counted and whether the OS-level parent-fork count tracks the Python
        before_fork count. A divergence (parent_gap > 0 or bare_fork_live_runtime
        > 0) is the signature of a fork that bypassed os.register_at_fork.
        """
        now = time.monotonic()
        if not force and (now - NativeRuntime._last_heartbeat_ts) < _HEARTBEAT_INTERVAL_S:
            return
        NativeRuntime._last_heartbeat_ts = now
        os_prepare, os_parent, os_child, bare = self._native_fork_counts()
        gap = (os_parent - NativeRuntime.before_fork_calls) if os_parent >= 0 else -1
        sys.stderr.write(
            "ddtrace fork-diag heartbeat (%s pid=%d): "
            "py(before=%d after_parent=%d after_child=%d skipped=%d) "
            "os_forks(prepare=%d parent=%d child=%d) bare_fork_live_runtime=%d parent_gap=%d\n"
            % (
                where,
                os.getpid(),
                NativeRuntime.before_fork_calls,
                NativeRuntime.after_fork_parent_calls,
                NativeRuntime.after_fork_child_calls,
                NativeRuntime.before_fork_skipped,
                os_prepare,
                os_parent,
                os_child,
                bare,
                gap,
            )
        )
        sys.stderr.flush()

    def before_fork(self) -> None:
        NativeRuntime.before_fork_calls += 1
        self._before_fork_ran = True
        super().before_fork()
        self._heartbeat("before_fork")

    def after_fork_parent(self) -> None:
        NativeRuntime.after_fork_parent_calls += 1
        self._before_fork_ran = False
        super().after_fork_parent()
        # Parent side runs on every os.fork(); a good place to notice that the
        # OS has serviced more forks than our before_fork hook saw.
        self._report_fork_divergence("after_fork_parent")
        self._heartbeat("after_fork_parent")

    def _native_fork_counts(self):
        """Best-effort read of the native OS-level fork counters.

        Returns (os_prepare, os_parent, os_child, bare_fork_live_runtime), using
        -1 for any value the (possibly older) native extension cannot provide.
        """
        try:
            os_prepare, os_parent, os_child = self.os_fork_counts()
        except Exception:
            os_prepare = os_parent = os_child = -1
        try:
            bare = self.bare_fork_live_runtime()
        except Exception:
            bare = -1
        return os_prepare, os_parent, os_child, bare

    def _report_fork_divergence(self, where: str) -> None:
        """Log when forks bypassed the ddtrace before-fork hook.

        Two independent signals of the same thing:
        - ``bare_fork_live_runtime``: a child fork inherited a *live* runtime
          because ``before_fork`` never parked it (native, child side).
        - ``parent_gap``: the OS reported more parent-side forks than our
          ``before_fork`` hook ran (parent side).
        Both advance only for forks that skip ``os.register_at_fork``.
        """
        os_prepare, os_parent, os_child, bare = self._native_fork_counts()
        gap = (os_parent - NativeRuntime.before_fork_calls) if os_parent >= 0 else -1
        if bare <= NativeRuntime._last_bare_reported and gap <= NativeRuntime._last_gap_reported:
            return
        NativeRuntime._last_bare_reported = max(NativeRuntime._last_bare_reported, bare)
        NativeRuntime._last_gap_reported = max(NativeRuntime._last_gap_reported, gap)
        log.warning(
            "native runtime: fork(s) bypassed the ddtrace before-fork hook (%s); "
            "child inherited a live tokio runtime. bare_fork_live_runtime=%d parent_gap=%d "
            "os_forks(prepare=%d parent=%d child=%d) "
            "py(before=%d after_parent=%d after_child=%d skipped=%d)",
            where,
            bare,
            gap,
            os_prepare,
            os_parent,
            os_child,
            NativeRuntime.before_fork_calls,
            NativeRuntime.after_fork_parent_calls,
            NativeRuntime.after_fork_child_calls,
            NativeRuntime.before_fork_skipped,
        )

    def after_fork_child(self) -> None:
        NativeRuntime.after_fork_child_calls += 1
        skipped = not self._before_fork_ran
        self._before_fork_ran = False
        try:
            super().after_fork_child()
        finally:
            if skipped:
                NativeRuntime.before_fork_skipped += 1
                # Ground-truth from the native side: how many times a stale
                # inherited runtime actually had to be forgotten. Available only
                # once the instrumented native extension is built; guard for
                # older builds.
                try:
                    native_forgotten = self.stale_runtimes_forgotten()
                except Exception:
                    native_forgotten = -1
                os_prepare, os_parent, os_child, bare = self._native_fork_counts()
                log.warning(
                    "native runtime: before_fork was skipped for this fork "
                    "(py_skipped=%d native_forgotten=%s bare_fork_live_runtime=%d "
                    "os_forks(prepare=%d parent=%d child=%d) after_child_calls=%d before_calls=%d); "
                    "child inherited a live tokio runtime and had to abandon it",
                    NativeRuntime.before_fork_skipped,
                    native_forgotten,
                    bare,
                    os_prepare,
                    os_parent,
                    os_child,
                    NativeRuntime.after_fork_child_calls,
                    NativeRuntime.before_fork_calls,
                )
            # Child side: also surface bypassing forks observed by this worker.
            self._report_fork_divergence("after_fork_child")
            self._heartbeat("after_fork_child")

    def _atexit(self) -> None:
        try:
            self.shutdown(timeout_ms=_DEFAULT_SHUTDOWN_TIMEOUT_MS)
        except Exception:
            log.debug("Error shutting down native runtime at exit", exc_info=True)

    def shutdown(self, timeout_ms: Optional[int] = None) -> None:
        """Shut down the shared Tokio runtime.

        Args:
            timeout_ms: Maximum time in milliseconds to wait for shutdown.
                If None, waits indefinitely — only safe if all workers have
                already been stopped (e.g. via TraceExporter.shutdown).
        """
        super().shutdown(timeout_ms=timeout_ms)
        atexit.unregister(self._atexit)
        forksafe.unregister_before_fork(self.before_fork)
        forksafe.unregister_parent(self.after_fork_parent)
        forksafe.unregister(self.after_fork_child)


def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    if NativeRuntime._instance is None:
        NativeRuntime._instance = NativeRuntime()
    return NativeRuntime._instance
