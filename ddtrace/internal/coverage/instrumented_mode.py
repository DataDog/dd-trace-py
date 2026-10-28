"""Instrumented coverage mode: engine-backed code instrumentation.

This is the default mode: sys.monitoring on 3.12+, bytecode injection on
3.10/3.11, legacy settrace on 3.9 (see engine.py for the ladder). It delegates to
CollectorContract's instrumented-mode machinery, which predates the mode
protocol — the methods here are a thin routing layer, not a reimplementation.

Deleting this file (to keep only import-only) means also deleting the engine
ladder and the collector's instrumented methods (instrument_code, hook_line,
hook dispatch, seen set, per-line reporting). The shared machinery — coverage
stores, import-time metadata, context stack, closure expansion via the global
edge map, file-level reporting — stays.
"""

from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.engine import CoverageCollector
from ddtrace.internal.coverage.engine import CoverageEngine
from ddtrace.internal.coverage.engine import create_engine
from ddtrace.internal.coverage.mode import CollectorContract
from ddtrace.internal.coverage.mode import ImportName


class InstrumentedCoverage:
    """Engine-backed instrumentation mode."""

    def __init__(self, collector: "CollectorContract") -> None:
        self._collector = collector
        self._engine: CoverageEngine = create_engine(t.cast(CoverageCollector, collector))
        self._seen: set[tuple[CodeType, str]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def install(self) -> None:
        """The engine is constructed in __init__; nothing to wire here."""

    def uninstall(self) -> None:
        """Nothing to undo (the shared uninstall handles hooks and exec)."""

    # ------------------------------------------------------------------
    # Instrumentation
    # ------------------------------------------------------------------

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        """Instrument a code object and track its executable lines.

        Moved from the collector: the dedup set and per-line tracking are
        instrumented-mode concerns (import-only writes a file-level sentinel
        instead of tracking lines).
        """
        from ddtrace.internal.utils.obfuscation import is_obfuscated_code

        if is_obfuscated_code(code):
            return code

        if (code, code.co_filename) in self._seen:
            return code
        self._seen.add((code, code.co_filename))

        new_code, lines = self._engine.instrument(code, code.co_filename, module.__package__ or "")
        self._seen.add((new_code, code.co_filename))
        self._collector.lines[code.co_filename].update(lines)
        return new_code

    def after_module_context(self, code: CodeType, module: ModuleType) -> None:
        # The engine records empty modules into the just-entered import context
        # (the caller enters the context before calling this).
        self._engine.on_empty_module(code, module.__package__ or "" if module is not None else "")

    def on_module_body_finished(self, module: ModuleType) -> None:
        """Nothing to do: instrumented modes record lines via events during the body."""

    def arm(self, import_context: bool) -> None:
        self._engine.arm(import_context=import_context)

    # ------------------------------------------------------------------
    # Context closures
    # ------------------------------------------------------------------

    def expand_context_lines(
        self, covered_lines: dict[str, CoverageLines], ctx_import_names: dict[str, set[ImportName]]
    ) -> None:
        self._collector._add_import_time_lines(covered_lines)

    def expand_context_files(
        self, covered_files: set[str], ctx_import_names: dict[str, set[ImportName]]
    ) -> t.AbstractSet[str]:
        return t.cast(t.AbstractSet[str], self._collector._get_covered_file_paths_with_imports(covered_files))

    # ------------------------------------------------------------------
    # Import-event recording (no wrapper in this mode; never called)
    # ------------------------------------------------------------------

    def record_import_file(self, path: str) -> None:
        """Not called: no import-event wrapper is installed in instrumented mode."""

    def record_import_edge(self, path: str, import_name: ImportName) -> None:
        """Not called: import edges are recorded by the engine's static analysis
        (PY_START-time import names) or injected import hooks.
        """

    def should_track(self, path: str) -> bool:
        return False

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def on_import_metadata_changed(self) -> None:
        """Nothing mode-derived to drop: the collector clears its own caches in
        _invalidate_import_metadata().
        """

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_import_only(self) -> bool:
        return False
