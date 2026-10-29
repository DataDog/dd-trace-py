from collections.abc import Callable
from collections.abc import Generator
import importlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import AbstractSet

import bm


class ImportOnly(bm.Scenario):  # type: ignore[misc]
    """Warm cached imports and test-context expansion; setup is outside timing."""

    operation: str
    nmodules: int

    def run(self) -> Generator[Callable[[int], None], None, None]:
        previous = os.environ.get("_DD_COVERAGE_IMPORT_ONLY")
        os.environ["_DD_COVERAGE_IMPORT_ONLY"] = "1"
        from ddtrace.internal.coverage.code import ModuleCodeCollector

        with TemporaryDirectory() as directory:
            root = Path(directory)
            names = [f"bench_import_only_{index}" for index in range(self.nmodules)]
            for index, name in enumerate(names):
                source = (
                    "value = 1\n"
                    if index < 2
                    else (
                        f"from {names[0]} import value\n"
                        f"def load():\n    from {names[1]} import value\n    return value\n"
                    )
                )
                (root / f"{name}.py").write_text(source)
            sys.path.insert(0, directory)
            try:
                ModuleCodeCollector.install(include_paths=[root])
                modules = [importlib.import_module(name) for name in names]
                callers = modules[2:]

                def collect(module: ModuleType) -> AbstractSet[str]:
                    context: ModuleCodeCollector.CollectInContext
                    with ModuleCodeCollector.CollectInContext() as context:
                        context.anchor_file(str(module.__file__))
                        module.load()
                    return context.get_covered_file_paths()

                # Verify attribution as well as timing, and warm shared metadata.
                if not {modules[0].__file__, modules[1].__file__, callers[0].__file__} <= collect(callers[0]):
                    raise RuntimeError("Import-only benchmark missed expected files")
                if self.operation == "imports":

                    def run(loops: int) -> None:
                        for _ in range(loops):
                            callers[0].load()
                elif self.operation == "contexts":

                    def run(loops: int) -> None:
                        for index in range(loops):
                            collect(callers[index % len(callers)])
                else:
                    raise ValueError(f"Unknown operation: {self.operation}")

                yield run
            finally:
                ModuleCodeCollector.uninstall()
                sys.path.remove(directory)
                for name in names:
                    sys.modules.pop(name, None)
                if previous is None:
                    os.environ.pop("_DD_COVERAGE_IMPORT_ONLY", None)
                else:
                    os.environ["_DD_COVERAGE_IMPORT_ONLY"] = previous
