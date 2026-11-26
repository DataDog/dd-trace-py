"""Instrumented coverage mode: the default, using the version-specific instrumentation ladder.

This module holds the code-object dedup and per-line bookkeeping that the old
code.py carried inline, moved here so the collector only sees the mode protocol.
The instrumentation dispatch (instrumentation.py) and version-specific modules
are unchanged from the previous structure.
"""

from pathlib import Path
import sys
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage import native_lines
from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.coverage.mode import CollectorContract
from ddtrace.internal.coverage.mode import CoverageMode
from ddtrace.internal.coverage.mode import ImportName
from ddtrace.internal.coverage.util import resolved_path
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.obfuscation import is_obfuscated_code


log = get_logger(__name__)


if t.TYPE_CHECKING:
    from ddtrace.internal.coverage.coverage_lines import CoverageLines


_PY_GE_312 = sys.version_info >= (3, 12)
_PY_GE_313 = sys.version_info >= (3, 13)
_EMPTY_MODULE_BYTES = compile("", "<empty>", "exec").co_code


class InstrumentedCoverage(CoverageMode):
    """Default mode: instrument code objects via the version-specific ladder."""

    def __init__(self, collector: CollectorContract) -> None:
        self._collector = collector
        # Code objects already instrumented, by identity: code objects compare by value, so a module
        # imported again (after it was removed from sys.modules) would otherwise count as instrumented.
        self._seen: dict[int, CodeType] = {}

    def install(self) -> None:
        """The instrumentation modules are imported at module level; nothing to wire."""

    def uninstall(self) -> None:
        """Nothing to undo (the shared uninstall handles hooks and exec)."""

    def instrument_module(self, code: CodeType, module: t.Optional[ModuleType]) -> CodeType:
        if native_lines.enabled():
            return self._instrument_module_natively(code)
        if module is None:
            return code
        if is_obfuscated_code(code):
            log.warning(
                "Cannot instrument %r for coverage: code object appears to be obfuscated (e.g. by PyArmor)",
                code.co_name,
            )
            return code
        if self._seen.get(id(code)) is code:
            return code
        self._seen[id(code)] = code
        # Canonicalize the path once at instrumentation entry: the hooks, the
        # executable-line bookkeeping, and the import-time metadata all use the
        # same key, so symlinked workspaces don't split the key space.
        canonical_path = resolved_path(code.co_filename)
        new_code, lines = instrument_all_lines(code, self._collector.hook, canonical_path, module.__package__ or "")
        self._seen[id(new_code)] = new_code
        self._collector.lines[canonical_path].update(lines)
        return new_code

    def _instrument_module_natively(self, code: CodeType) -> CodeType:
        """Enable native LINE events on the module's code tree, which runs unchanged.

        Native collection reports executed lines only, so no executable lines are recorded.
        """
        if self._seen.get(id(code)) is code:
            return code
        self._seen[id(code)] = code
        # The native walk checks every code object for obfuscation and skips the ones that look
        # obfuscated, with the code nested in them.
        if native_lines.instrument(code, resolved_path(code.co_filename)) < 0:
            log.warning(
                "Cannot instrument %r for coverage: code object appears to be obfuscated (e.g. by PyArmor)",
                code.co_name,
            )
        return code

    def after_module_context(self, code: CodeType, module: t.Optional[ModuleType]) -> None:
        # Python 3.13+ doesn't fire LINE events for empty modules. Call the hook
        # directly so coverage is recorded into the just-entered import context.
        # Native line collection opens no import context and reports executed lines only.
        # co_code builds a new bytes object, so it is read last.
        if module is None or native_lines.enabled():
            return
        if _PY_GE_313 and code.co_code == _EMPTY_MODULE_BYTES:
            package = module.__package__ or ""
            import_info = (package, ("",)) if package else None
            self._collector.hook((0, resolved_path(code.co_filename), import_info))

    def on_module_body_finished(self, module: ModuleType) -> None:
        """Nothing to do: instrumented modes record lines via events during the body."""

    def arm(self, import_context: bool) -> None:
        # Python 3.12+ re-arm sys.monitoring events for a new coverage context.
        # Native line collection re-arms when its context is pushed.
        if _PY_GE_312 and not native_lines.enabled():
            # Version-gated: mypy lints under 3.10 where instrumentation_py3_12
            # and sys.monitoring are unreachable. The logic is identical to the
            # CollectInContext.__enter__ code it replaces (see main's code.py).
            from ddtrace.internal.coverage.instrumentation_py3_12 import (  # type: ignore[attr-defined]
                update_disable_optimization,
            )

            if update_disable_optimization():
                sys.monitoring.restart_events()  # type: ignore[attr-defined]

    def expand_context_lines(
        self, covered_lines: "dict[str, CoverageLines]", ctx_import_names: "dict[str, set[ImportName]]"
    ) -> None:
        self._collector._add_import_time_lines(covered_lines)

    def expand_context_files(
        self, covered_files: "set[str]", ctx_import_names: "dict[str, set[ImportName]]"
    ) -> "t.AbstractSet[str]":
        return self._collector._get_covered_file_paths_with_imports(covered_files)

    def record_import_file(self, path: str) -> None:
        """Not called: no import-event wrapper in instrumented mode."""

    def record_import_edge(self, path: str, import_name: ImportName) -> None:
        """Not called: import edges are recorded by the engine's static analysis."""

    def should_track(self, path: str) -> bool:
        return False

    def anchor_test_file(self, test_file: Path, ctx: t.Any) -> None:
        """No-op: instrumented modes cover the test file via execution events."""

    def on_import_metadata_changed(self) -> None:
        """Nothing mode-derived to drop."""

    is_import_only = False
