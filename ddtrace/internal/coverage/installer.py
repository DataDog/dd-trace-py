from pathlib import Path
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.multiprocessing_coverage import _patch_multiprocessing


def install(include_paths: t.Optional[t.List[Path]] = None) -> None:
    ModuleCodeCollector.install(include_paths=include_paths)
    _patch_multiprocessing()
