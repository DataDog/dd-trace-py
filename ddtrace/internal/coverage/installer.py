from pathlib import Path
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.instrumentation import register_coverage
from ddtrace.internal.coverage.multiprocessing_coverage import _patch_multiprocessing
from ddtrace.internal.coverage.threading_coverage import _patch_threading


def install(include_paths: t.Optional[list[Path]] = None, collect_import_time_coverage: bool = False) -> None:
    # Eagerly claim COVERAGE_ID before setting up the import hook.
    # If another tool already holds the slot, register_coverage() returns False
    # and instrument_all_lines() will be a no-op for all subsequent imports.
    register_coverage()
    ModuleCodeCollector.install(include_paths=include_paths, collect_import_time_coverage=collect_import_time_coverage)
    _patch_multiprocessing()
    _patch_threading()
