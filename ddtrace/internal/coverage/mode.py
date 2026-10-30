"""Shared mode boundary; neither coverage implementation imports the collector.

Keep instrumentation and context expansion in each implementation. The collector
selects one mode and delegates; removing a mode only changes that selection.
"""

from collections import defaultdict
from pathlib import Path
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.coverage_lines import CoverageLines


ImportName = tuple[str, tuple[str, ...]]


class CollectorContract(t.Protocol):
    lines: defaultdict[str, CoverageLines]

    def hook(self, arg: tuple[int, str, t.Optional[tuple[str, tuple[str, ...]]]]) -> None:
        """Event dispatch: instrumentation engines call this for each covered event."""

    _import_time_covered: defaultdict[str, CoverageLines]

    def hook_import(self, path: str, import_name: ImportName) -> None: ...
    def _record_file_covered(self, path: str) -> None: ...
    def _record_import_time_sentinel(self, module_file: str) -> None: ...
    def _register_import_time_name(self, name: str, module_file: str) -> None: ...
    def _is_instrumentable_path(self, code_path: Path) -> bool: ...
    def _get_dependency_paths_for_import_name(self, import_name: ImportName) -> frozenset[str]: ...
    def _add_import_time_lines(self, covered_lines: dict[str, CoverageLines]) -> None: ...
    def _get_covered_file_paths_with_imports(self, covered_file_paths: set[str]) -> t.AbstractSet[str]: ...


class CoverageMode:
    """Mode-owned instrumentation and expansion, with optional lifecycle hooks."""

    is_import_only = False

    def __init__(self, collector: CollectorContract) -> None:
        self._collector = collector

    def install(self) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def arm(self, import_context: bool) -> None:
        pass

    def after_module_context(self, code: CodeType, module: ModuleType) -> None:
        """Called after entering the import context, before executing the body."""

    def on_module_body_finished(self, module: ModuleType) -> None:
        """Called when the module body produced no instrumented lines."""

    def on_import_metadata_changed(self) -> None:
        """Invalidate mode-owned caches when module names or coverage change."""

    def anchor_test_file(self, test_file: Path, anchor: t.Callable[[str], None]) -> None:
        """Anchor a test's file as a coverage closure root (import-only overrides)."""

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        raise NotImplementedError

    def expand_context_lines(
        self, covered_lines: dict[str, CoverageLines], import_names: dict[str, set[ImportName]]
    ) -> None:
        raise NotImplementedError

    def expand_context_files(self, paths: set[str], import_names: dict[str, set[ImportName]]) -> t.AbstractSet[str]:
        raise NotImplementedError
