import sys


# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 16):
    from ddtrace.internal.coverage.instrumentation_py3_16 import instrument_all_lines  # noqa
    from ddtrace.internal.coverage.instrumentation_py3_16 import register_coverage  # noqa
    from ddtrace.internal.coverage.instrumentation_py3_16 import unregister_coverage  # noqa
elif sys.version_info >= (3, 12):
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines  # noqa
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage  # noqa
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage  # noqa
elif sys.version_info >= (3, 11):
    from ddtrace.internal.coverage.instrumentation_py3_11 import instrument_all_lines  # noqa

    def register_coverage() -> bool:  # noqa: E306
        return True  # sys.monitoring not available on Python < 3.12

    def unregister_coverage() -> None:  # noqa: E306
        pass  # sys.monitoring not available on Python < 3.12

elif sys.version_info >= (3, 10):
    from ddtrace.internal.coverage.instrumentation_py3_10 import instrument_all_lines  # noqa

    def register_coverage() -> bool:  # noqa: E306
        return True  # sys.monitoring not available on Python < 3.12

    def unregister_coverage() -> None:  # noqa: E306
        pass  # sys.monitoring not available on Python < 3.12

else:
    from ddtrace.internal.coverage.instrumentation_py3_9 import instrument_all_lines  # noqa

    def register_coverage() -> bool:  # noqa: E306
        return True  # sys.monitoring not available on Python < 3.12

    def unregister_coverage() -> None:  # noqa: E306
        pass  # sys.monitoring not available on Python < 3.12
