import sys

from ddtrace.internal.coverage.util import is_file_level_coverage_enabled


# Imports are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 16):
    from ddtrace.internal.coverage.instrumentation_py3_16 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 12):
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 11):
    if is_file_level_coverage_enabled():
        # POC: file-level coverage on 3.11 routes through the shared bytecode-injection
        # framework (same implementation as 3.10) instead of the hand-rolled line-level
        # module. Line-level collection keeps the existing instrumentation_py3_11 path.
        from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa
    else:
        from ddtrace.internal.coverage.instrumentation_py3_11 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 10):
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa
else:
    from ddtrace.internal.coverage.instrumentation_py3_9 import instrument_all_lines  # noqa
