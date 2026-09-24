import importlib
from pathlib import Path
import sys
from typing import Any


def exercise_context(modules: list[Any], context_index: int, modules_per_context: int) -> int:
    checksum = context_index
    for offset in range(modules_per_context):
        module = modules[(context_index + offset) % len(modules)]
        for function in module.FUNCTIONS:
            checksum = function(checksum) & 0xFFFF
    return checksum


def main() -> None:
    corpus = Path(sys.argv[1])
    ncontexts = int(sys.argv[2])
    nmodules = int(sys.argv[3])
    modules_per_context = int(sys.argv[4])
    collect_coverage = sys.argv[5:] == ["--coverage"]

    if collect_coverage and sys.version_info < (3, 12):
        raise RuntimeError("file-level sys.monitoring coverage requires Python 3.12+")

    sys.path.insert(0, str(corpus))

    if collect_coverage:
        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        install(
            include_paths=[corpus],
            collect_import_time_coverage=True,
        )

    modules: list[Any] = [importlib.import_module(f"bench_module_{index}") for index in range(nmodules)]

    checksum = 0
    for context_index in range(ncontexts):
        if collect_coverage:
            with ModuleCodeCollector.CollectInContext() as collector:
                checksum ^= exercise_context(modules, context_index, modules_per_context)
                covered_paths = collector.get_covered_file_paths()
                expected_paths = {
                    modules[(context_index + offset) % len(modules)].__file__ for offset in range(modules_per_context)
                }
                if missing_paths := expected_paths.difference(covered_paths):
                    raise AssertionError(f"file-level coverage missed executed modules: {sorted(missing_paths)}")
        else:
            checksum ^= exercise_context(modules, context_index, modules_per_context)

    if checksum < 0:
        raise AssertionError("unreachable checksum")


if __name__ == "__main__":
    main()
