"""
Runtime code coverage collector for dead code detection in staging environments.

Instruments application code at import time using ModuleCodeCollector (sys.monitoring
on Python 3.12+). Each line's callback fires at most once — after process warmup the
marginal overhead is near-zero. Reports are written to disk periodically and on exit.

AIDEV-NOTE: This intentionally reuses ModuleCodeCollector WITHOUT CollectInContext /
restart_events(), so sys.monitoring.DISABLE is permanent per line. That's exactly
what we want: once a line is hit, it's free forever. This differs from the test-
coverage use-case where traps are re-armed between tests.
"""

import atexit
import os
from pathlib import Path
import sys
import threading
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# AIDEV-NOTE: sys.monitoring is required — bytecode rewriting on 3.10/3.11 fires
# the hook on every hit (not just the first), making it unsuitable for production.
_REQUIRED_PYTHON = (3, 12)


class RuntimeCoverageCollector:
    """Collects runtime coverage data to identify dead code. Writes JSON reports to disk."""

    _instance: t.Optional["RuntimeCoverageCollector"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        include_paths: list[Path],
        output_dir: Path,
        flush_interval: float,
        workspace_path: Path,
    ) -> None:
        self._include_paths = include_paths
        self._output_dir = output_dir
        self._flush_interval = flush_interval
        self._workspace_path = workspace_path
        self._stop_event = threading.Event()
        self._flush_thread: t.Optional[threading.Thread] = None

    @classmethod
    def enable(
        cls,
        include_paths: t.Optional[list[Path]] = None,
        output_dir: t.Optional[Path] = None,
        flush_interval: float = 300.0,
        workspace_path: t.Optional[Path] = None,
    ) -> None:
        if sys.version_info < _REQUIRED_PYTHON:
            log.warning(
                "DD_RUNTIME_COVERAGE_ENABLED requires Python %d.%d+, got %s — skipping",
                *_REQUIRED_PYTHON,
                sys.version.split()[0],
            )
            return

        with cls._lock:
            if cls._instance is not None:
                log.debug("RuntimeCoverageCollector already enabled")
                return

            cwd = Path(os.getcwd())
            instance = cls(
                include_paths=include_paths if include_paths is not None else [cwd],
                output_dir=output_dir if output_dir is not None else Path("/tmp"),
                flush_interval=flush_interval,
                workspace_path=workspace_path if workspace_path is not None else cwd,
            )
            cls._instance = instance

        instance._start()

    def _start(self) -> None:
        # AIDEV-NOTE: ModuleCodeCollector.install() hooks sys.meta_path so that every
        # subsequent import of a matching path is instrumented before execution. Modules
        # already loaded at this point are NOT re-instrumented — start as early as possible.
        from ddtrace.internal.coverage.code import ModuleCodeCollector

        ModuleCodeCollector.install(include_paths=self._include_paths)
        ModuleCodeCollector.start_coverage()

        atexit.register(self._flush)

        if self._flush_interval > 0:
            self._flush_thread = threading.Thread(
                target=self._flush_loop,
                name="dd-runtime-coverage-flusher",
                daemon=True,
            )
            self._flush_thread.start()

        log.debug(
            "Runtime coverage collector started (output_dir=%s, flush_interval=%ss)",
            self._output_dir,
            self._flush_interval,
        )

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(timeout=self._flush_interval):
            self._flush()

    def _flush(self) -> None:
        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.runtime_coverage.writer import write_coverage_report

        mcc = ModuleCodeCollector._instance
        if mcc is None:
            return

        write_coverage_report(
            executable_lines=dict(mcc.lines),
            covered_lines=dict(mcc.covered),
            output_dir=self._output_dir,
            workspace_path=self._workspace_path,
        )

    @classmethod
    def disable(cls) -> None:
        """Stop the collector and flush a final report. Intended for tests."""
        with cls._lock:
            instance = cls._instance
            if instance is None:
                return
            cls._instance = None

        instance._stop_event.set()
        instance._flush()

        from ddtrace.internal.coverage.code import ModuleCodeCollector

        ModuleCodeCollector.stop_coverage()
