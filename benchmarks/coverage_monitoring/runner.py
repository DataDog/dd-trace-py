from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def exercise_context(modules: list[Any], context_index: int, modules_per_context: int) -> int:
    checksum = context_index
    for offset in range(modules_per_context):
        module = modules[(context_index + offset) % len(modules)]
        for function in module.FUNCTIONS:
            checksum = function(checksum) & 0xFFFF
    return checksum


def start_exception_consumers(stack: ExitStack) -> None:
    from ddtrace.errortracking._handled_exceptions import monitoring_reporting
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.exception import ExceptionCollector

    if not ddup.is_available:
        raise RuntimeError("ddup is required for the combined monitoring benchmark")
    output_prefix = os.path.join(tempfile.gettempdir(), "ddtrace-combined-monitoring")
    ddup.config(env="benchmark", service="combined-monitoring", version="1", output_filename=output_prefix)
    ddup.start()

    monitoring_reporting._install_sys_monitoring_reporting()
    uninstall_reporting = getattr(monitoring_reporting, "_uninstall_sys_monitoring_reporting", None)
    if uninstall_reporting is not None:
        stack.callback(uninstall_reporting)
    stack.enter_context(ExceptionCollector(sampling_interval=100, collect_message=False))


def main() -> None:
    corpus = Path(sys.argv[1])
    ncontexts = int(sys.argv[2])
    nmodules = int(sys.argv[3])
    modules_per_context = int(sys.argv[4])
    options = sys.argv[5:]
    collect_coverage = "--coverage" in options
    exception_consumers = "--exception-consumers" in options
    startup_order = options[options.index("--exception-consumers") + 1] if exception_consumers else "coverage_first"

    if collect_coverage and sys.version_info < (3, 12):
        raise RuntimeError("file-level sys.monitoring coverage requires Python 3.12+")

    sys.path.insert(0, str(corpus))

    with ExitStack() as stack:
        if exception_consumers and startup_order == "exceptions_first":
            start_exception_consumers(stack)

        if collect_coverage:
            from ddtrace.internal.coverage.code import ModuleCodeCollector
            from ddtrace.internal.coverage.installer import install

            install(
                include_paths=[corpus],
                collect_import_time_coverage=True,
            )

        if exception_consumers and startup_order == "coverage_first":
            start_exception_consumers(stack)

        modules: list[Any] = [importlib.import_module(f"bench_module_{index}") for index in range(nmodules)]

        checksum = 0
        for context_index in range(ncontexts):
            if collect_coverage:
                with ModuleCodeCollector.CollectInContext() as collector:
                    checksum ^= exercise_context(modules, context_index, modules_per_context)
                    covered_paths = collector.get_covered_file_paths()
                    expected_paths = {
                        modules[(context_index + offset) % len(modules)].__file__
                        for offset in range(modules_per_context)
                    }
                    if missing_paths := expected_paths.difference(covered_paths):
                        raise AssertionError(f"file-level coverage missed executed modules: {sorted(missing_paths)}")
            else:
                checksum ^= exercise_context(modules, context_index, modules_per_context)

    if checksum < 0:
        raise AssertionError("unreachable checksum")


if __name__ == "__main__":
    main()
