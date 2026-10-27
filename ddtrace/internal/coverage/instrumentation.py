import sys


# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 16):
    from ddtrace.internal.coverage.instrumentation_py3_16 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 12):
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 10):
    # Python 3.10 and 3.11 share the bytecode-injection framework implementation; the
    # file-level vs line-level mode is decided inside instrumentation_py3_10 based on
    # _DD_COVERAGE_FILE_LEVEL (see ddtrace.internal.coverage.util).
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa
else:
    from ddtrace.internal.coverage.instrumentation_py3_9 import instrument_all_lines  # noqa
