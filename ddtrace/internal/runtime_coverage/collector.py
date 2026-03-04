"""
Runtime code coverage collector for dead code detection in staging environments.

Instruments application code at import time using sys.monitoring (Python 3.12+).
Each line's callback fires at most once — after process warmup the marginal
overhead is near-zero. Reports are written to disk periodically and on exit.

AIDEV-NOTE: This uses a standalone sys.monitoring instrumentation module
(runtime_coverage.instrumentation) instead of the shared ModuleCodeCollector.
sys.monitoring.DISABLE is permanent per line — once a line is hit, it's free
forever. No import dependency tracking, no hook indirection.
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
        # AIDEV-NOTE: _RuntimeCoverageWatchdog hooks sys.meta_path so that every
        # subsequent import of a matching path is instrumented before execution.
        # Modules already loaded at this point are NOT re-instrumented — start as
        # early as possible.
        _RuntimeCoverageWatchdog.install(
            include_paths=self._include_paths,
        )

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
        from ddtrace.internal.runtime_coverage.instrumentation import get_covered_lines
        from ddtrace.internal.runtime_coverage.instrumentation import get_executable_lines
        from ddtrace.internal.runtime_coverage.writer import write_coverage_report

        write_coverage_report(
            executable_lines=dict(get_executable_lines()),
            covered_lines=dict(get_covered_lines()),
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

        from ddtrace.internal.runtime_coverage.instrumentation import reset

        reset()
        _RuntimeCoverageWatchdog.uninstall()


# ── Import hook ─────────────────────────────────────────────────────────────


class _RuntimeCoverageWatchdog:
    """Minimal sys.meta_path hook that instruments code objects on import via
    the standalone sys.monitoring instrumentation module.

    AIDEV-NOTE: This intentionally does NOT subclass ModuleWatchdog/BaseModuleWatchdog
    to avoid pulling in the full ModuleCodeCollector machinery. It's a thin
    MetaPathFinder that delegates to instrumentation.instrument().
    """

    _instance: t.Optional["_RuntimeCoverageWatchdog"] = None
    _include_paths: list[Path] = []
    _exclude_paths: list[Path] = []

    @classmethod
    def install(cls, include_paths: list[Path]) -> None:
        if cls._instance is not None:
            return

        from ddtrace.internal.packages import platlib_path
        from ddtrace.internal.packages import platstdlib_path
        from ddtrace.internal.packages import purelib_path
        from ddtrace.internal.packages import stdlib_path

        instance = cls()
        instance._include_paths = include_paths
        instance._exclude_paths = [
            stdlib_path,
            platstdlib_path,
            platlib_path,
            purelib_path,
            Path(__file__).resolve().parent,  # don't instrument ourselves
        ]
        cls._instance = instance

        # Register as a meta_path finder — must be first to intercept before
        # the default finders.
        sys.meta_path.insert(0, instance)  # type: ignore[arg-type]
        log.debug("_RuntimeCoverageWatchdog installed")

    @classmethod
    def uninstall(cls) -> None:
        instance = cls._instance
        if instance is None:
            return
        try:
            sys.meta_path.remove(instance)  # type: ignore[arg-type]
        except ValueError:
            pass
        cls._instance = None
        log.debug("_RuntimeCoverageWatchdog uninstalled")

    def _should_instrument(self, path: Path) -> bool:
        if not any(path.is_relative_to(p) for p in self._include_paths):
            return False
        if any(path.is_relative_to(p) for p in self._exclude_paths):
            return False
        return True

    # ── MetaPathFinder protocol ─────────────────────────────────────────

    def find_module(self, fullname, path=None):
        # Python 3.12+ prefers find_spec; this is a fallback.
        return None

    def find_spec(self, fullname, path=None, target=None):
        """Intercept module loading to wrap the loader with our instrumenting loader."""
        if fullname in self._finding:
            return None

        self._finding.add(fullname)
        try:
            from importlib.util import find_spec

            try:
                spec = find_spec(fullname)
            except Exception:
                return None

            if spec is None or spec.loader is None:
                return None

            origin = getattr(spec, "origin", None)
            if origin is None:
                return None

            origin_path = Path(origin).resolve()
            if not self._should_instrument(origin_path):
                return None

            # Wrap the loader so we can instrument the code before execution.
            if not isinstance(spec.loader, _InstrumentingLoader):
                spec.loader = _InstrumentingLoader(spec.loader)  # type: ignore[assignment]

            return spec

        finally:
            self._finding.remove(fullname)

    def __init__(self):
        self._finding: set[str] = set()


class _InstrumentingLoader:
    """Wraps an existing loader to instrument code objects before execution."""

    def __init__(self, original_loader):
        self._original = original_loader
        # Proxy attributes that importlib expects.
        for attr in ("path", "archive", "submodule_search_locations"):
            if hasattr(original_loader, attr):
                setattr(self, attr, getattr(original_loader, attr))

    def create_module(self, spec):
        if hasattr(self._original, "create_module"):
            return self._original.create_module(spec)
        return None

    def exec_module(self, module):
        from ddtrace.internal.runtime_coverage.instrumentation import instrument

        # Try to get the code object to instrument it before execution.
        get_code = getattr(self._original, "get_code", None)
        if get_code is not None:
            try:
                code = get_code(module.__name__)
                if code is not None:
                    instrument(code)
            except Exception:
                log.debug("Failed to instrument %s", module.__name__, exc_info=True)

        # Always delegate execution to the original loader.
        return self._original.exec_module(module)

    def __getattr__(self, name):
        # Proxy everything else to the original loader.
        return getattr(self._original, name)
