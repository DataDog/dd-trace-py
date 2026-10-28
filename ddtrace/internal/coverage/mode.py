"""The coverage instrumentation mode contract.

The collector (code.py) owns all shared state (coverage data, import-time metadata,
context stack, dependency closures) and delegates every mode-specific decision to
exactly one CoverageMode implementation. The collector has no if/else for mode
selection: it calls `self._mode.X()` and the implementation decides.

Removing a mode means deleting its implementation file and the one registration
line in ModuleCodeCollector.__init__. The other mode is unaffected because the
collector only sees this interface.

Implementations:
    InstrumentedCoverage (instrumented_mode.py) - engine-backed code instrumentation
    ImportOnlyCoverage (import_only.py) - import-event-only coverage
"""

from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.coverage_lines import CoverageLines


ImportName = tuple[str, tuple[str, ...]]


class CoverageMode(t.Protocol):
    """Structural interface between ModuleCodeCollector and its instrumentation mode."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def install(self) -> None:
        """Wire up mode-specific instrumentation.

        Called once, after the collector's include paths and flags are configured.
        Import-only installs the import-event wrapper and backfills; instrumented
        modes need nothing here (the engine is constructed in __init__).
        """

    def uninstall(self) -> None:
        """Undo install()."""

    # ------------------------------------------------------------------
    # Instrumentation (called from transform)
    # ------------------------------------------------------------------

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        """Return the code object for a module about to execute its body.

        Instrumented modes rewrite the code and record executable lines; import-only
        returns it unchanged and writes a line-0 sentinel instead.
        """

    def after_module_context(self, code: CodeType, module: ModuleType) -> None:
        """Called after the import-time context is entered, before the module body runs.

        Instrumented modes use this for empty-module recording (Python 3.13+
        sys.monitoring does not fire LINE events for empty modules, so the engine
        records them explicitly into the just-entered context). Import-only is a
        no-op.
        """

    # ------------------------------------------------------------------
    # Module lifecycle (called from _finish_module_context)
    # ------------------------------------------------------------------

    def on_module_body_finished(self, module: ModuleType) -> None:
        """Bookkeeping when a module's import context closed with no covered lines.

        Only reached when nothing executed during the body. Import-only records the
        file-level sentinel so the module participates in the dependency closure;
        instrumented modes have nothing to do (lines were recorded by events).
        """

    # ------------------------------------------------------------------
    # Context re-arming (called from CollectInContext.__enter__)
    # ------------------------------------------------------------------

    def arm(self, import_context: bool) -> None:
        """Re-arm instrumentation events for a new coverage context.

        Instrumented modes forward to their engine (sys.monitoring restart_events,
        bytecode-probe resets); import-only arms nothing.
        """

    # ------------------------------------------------------------------
    # Context closure expansion (called from CollectInContext getters)
    # ------------------------------------------------------------------

    def expand_context_lines(
        self, covered_lines: dict[str, CoverageLines], ctx_import_names: dict[str, set[ImportName]]
    ) -> None:
        """Expand a context's covered lines with import-time dependencies.

        Instrumented modes use the global edge map; import-only uses per-context
        edge maps so tests sharing a module do not pollute each other.
        """

    def expand_context_files(
        self, covered_files: set[str], ctx_import_names: dict[str, set[ImportName]]
    ) -> t.AbstractSet[str]:
        """File-level variant of expand_context_lines."""

    # ------------------------------------------------------------------
    # Import-event recording (called from the wrapper; import-only only)
    # ------------------------------------------------------------------

    def record_import_file(self, path: str) -> None:
        """Record a file seen through an import event (import-event wrapper only)."""

    def record_import_edge(self, path: str, import_name: ImportName) -> None:
        """Record an import dependency edge with scope routing (wrapper only)."""

    def should_track(self, path: str) -> bool:
        """Whether an import-event file should be recorded (wrapper only)."""

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def on_import_metadata_changed(self) -> None:
        """Import-time metadata changed; drop mode-derived caches."""

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_import_only(self) -> bool:
        """True when coverage is collected exclusively from import events."""
