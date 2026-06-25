from pathlib import Path
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.multiprocessing_coverage import _patch_multiprocessing
from ddtrace.internal.coverage.threading_coverage import _patch_threading


def install(
    include_paths: t.Optional[list[Path]] = None,
    collect_import_time_coverage: bool = False,
    use_disable_optimization: bool = True,
) -> None:
    ModuleCodeCollector.install(
        include_paths=include_paths,
        collect_import_time_coverage=collect_import_time_coverage,
        use_disable_optimization=use_disable_optimization,
    )
    _patch_multiprocessing()
    _patch_threading()
