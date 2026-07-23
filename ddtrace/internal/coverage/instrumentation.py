import sys


# Import are noqa'd otherwise some formatters will helpfully remove them
def configure_file_level_coverage(enabled: object = None) -> bool:
    return bool(enabled)


if sys.version_info >= (3, 16):
    from ddtrace.internal.coverage.instrumentation_py3_16 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 12):
    from ddtrace.internal.coverage.instrumentation_py3_12 import configure_file_level_coverage  # noqa
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 11):
    from ddtrace.internal.coverage.instrumentation_py3_11 import instrument_all_lines  # noqa
elif sys.version_info >= (3, 10):
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa
else:
    from ddtrace.internal.coverage.instrumentation_py3_9 import instrument_all_lines  # noqa
