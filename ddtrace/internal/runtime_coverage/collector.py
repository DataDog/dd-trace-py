"""
Runtime code coverage collector for dead code detection in staging environments.

Instruments application code at import time using sys.monitoring (Python 3.12+).
Each line's callback fires at most once — after process warmup the marginal
overhead is near-zero. Reports are written to disk periodically and on exit.

AIDEV-NOTE: This uses a standalone sys.monitoring instrumentation module
(runtime_coverage.instrumentation) instead of the shared ModuleCodeCollector.
sys.monitoring.DISABLE is permanent per line — once a line is hit, it's free
forever. No import dependency tracking, no hook indirection.

AIDEV-NOTE: We use ModuleWatchdog.register_pre_exec_module_hook (NOT transform())
because SourceFileLoader.exec_module compiles its own code object internally and
never calls the Python-level get_code() wrapper that transform() relies on.
The pre-exec hook replaces exec_module entirely: we call get_code() ourselves,
instrument the code, then exec() it into the module's namespace.
"""

import atexit
import os
from pathlib import Path
import re
import sys
import threading
from types import ModuleType
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.packages import platlib_path
from ddtrace.internal.packages import platstdlib_path
from ddtrace.internal.packages import purelib_path
from ddtrace.internal.packages import stdlib_path


log = get_logger(__name__)

# AIDEV-NOTE: sys.monitoring is required — bytecode rewriting on 3.10/3.11 fires
# the hook on every hit (not just the first), making it unsuitable for production.
_REQUIRED_PYTHON = (3, 12)

_exclude_paths: list[Path] = [
    stdlib_path,
    platstdlib_path,
    platlib_path,
    purelib_path,
    # Exclude the entire ddtrace package — the tracer must never instrument itself.
    # Without this, importing instrumentation.py triggers a circular import because
    # its top-level imports (e.g. CoverageLines) go through ModuleWatchdog and
    # re-enter _pre_exec_hook before instrumentation.py has finished loading.
    Path(__file__).resolve().parent.parent.parent,  # ddtrace/
]

_include_paths: list[Path] = []

# Matches DD_VERSION values like "v100612292-865382c3-integration-data-science-watchdog-platform"
# where the second segment is a hex commit SHA.
_DD_VERSION_SHA_RE = re.compile(r"^v\d+-([0-9a-f]+)-")


def _extract_commit_sha_from_dd_version() -> t.Optional[str]:
    """Extract commit SHA from DD_VERSION if it follows the expected format."""
    dd_version = os.environ.get("DD_VERSION")
    if dd_version is None:
        return None
    m = _DD_VERSION_SHA_RE.match(dd_version)
    return m.group(1) if m else None


def _should_instrument(module_name: str) -> bool:
    """Condition for the pre-exec hook. Receives the module name, resolves
    its origin via the import system, and checks include/exclude paths.
    """
    module = sys.modules.get(module_name)
    if module is None:
        return False
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if origin is None:
        return False
    path = Path(origin).resolve()
    if not any(path.is_relative_to(p) for p in _include_paths):
        return False
    if any(path.is_relative_to(p) for p in _exclude_paths):
        return False
    return True


def _pre_exec_hook(loader, module: ModuleType) -> None:
    """Pre-exec hook: load code via get_code(), instrument it, then exec().

    AIDEV-NOTE: This replaces exec_module entirely. We must call get_code()
    ourselves because SourceFileLoader.exec_module uses an internal C-level
    get_code that bypasses Python-level monkey-patches (and transform()).
    """
    from ddtrace.internal.runtime_coverage.instrumentation import instrument

    get_code = getattr(loader.loader, "get_code", None)
    if get_code is not None:
        try:
            code = get_code(module.__name__)
            if code is not None:
                instrument(code)
                exec(code, module.__dict__)
                return
        except Exception:
            log.debug("Failed to instrument %s", module.__name__, exc_info=True)

    # Fallback: run the original exec_module
    loader.loader.exec_module(module)


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
        commit_sha: t.Optional[str] = None,
    ) -> None:
        self._include_paths = include_paths
        self._output_dir = output_dir
        self._flush_interval = flush_interval
        self._workspace_path = workspace_path
        self._commit_sha = commit_sha
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
                commit_sha=os.environ.get("DD_GIT_COMMIT_SHA") or _extract_commit_sha_from_dd_version(),
            )
            cls._instance = instance

        instance._start()

    def _start(self) -> None:
        global _include_paths

        _include_paths = [p.resolve() for p in self._include_paths]

        # AIDEV-NOTE: register_pre_exec_module_hook installs ModuleWatchdog if
        # not already installed, then registers our hook. The hook replaces
        # exec_module for matching modules so we can instrument the code object
        # that actually gets executed (not a throwaway copy).
        ModuleWatchdog.register_pre_exec_module_hook(
            _should_instrument,
            _pre_exec_hook,
        )

        # AIDEV-NOTE: The import hook only catches modules loaded via exec_module.
        # Code that bypasses the import system — most notably __main__ when running
        # `python script.py` — is never seen by ModuleWatchdog. We use a global
        # PY_START event to auto-instrument such code on first function entry.
        from ddtrace.internal.runtime_coverage.instrumentation import enable_auto_instrumentation

        enable_auto_instrumentation(
            include_paths=_include_paths,
            exclude_paths=[p.resolve() for p in _exclude_paths],
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
            commit_sha=self._commit_sha,
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
        ModuleWatchdog.remove_pre_exec_module_hook(_should_instrument, _pre_exec_hook)
