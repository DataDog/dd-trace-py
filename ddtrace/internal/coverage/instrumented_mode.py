"""Engine-backed line/file coverage, independent of import-only tracking."""

from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.engine import CoverageCollector
from ddtrace.internal.coverage.engine import create_engine
from ddtrace.internal.coverage.mode import CollectorContract
from ddtrace.internal.coverage.mode import CoverageMode
from ddtrace.internal.coverage.mode import ImportName
from ddtrace.internal.utils.obfuscation import is_obfuscated_code


class InstrumentedCoverage(CoverageMode):
    def __init__(self, collector: CollectorContract) -> None:
        super().__init__(collector)
        self._engine = create_engine(t.cast(CoverageCollector, collector))
        self._seen: set[tuple[CodeType, str]] = set()

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        if is_obfuscated_code(code) or (code, code.co_filename) in self._seen:
            return code
        self._seen.add((code, code.co_filename))
        new_code, lines = self._engine.instrument(code, code.co_filename, module.__package__ or "")
        self._seen.add((new_code, code.co_filename))
        self._collector.lines[code.co_filename].update(lines)
        return new_code

    def after_module_context(self, code: CodeType, module: ModuleType) -> None:
        # Python 3.13+ monitoring doesn't emit LINE events for empty modules.
        self._engine.on_empty_module(code, module.__package__ or "")

    def arm(self, import_context: bool) -> None:
        self._engine.arm(import_context=import_context)

    def expand_context_lines(
        self, covered_lines: dict[str, CoverageLines], import_names: dict[str, set[ImportName]]
    ) -> None:
        self._collector._add_import_time_lines(covered_lines)

    def expand_context_files(self, paths: set[str], import_names: dict[str, set[ImportName]]) -> t.AbstractSet[str]:
        return self._collector._get_covered_file_paths_with_imports(paths)
