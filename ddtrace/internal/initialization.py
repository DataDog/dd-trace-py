"""
Initialization logic for ddtrace components.
"""

from ddtrace.internal._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    from ddtrace.internal.compat import PYTHON_VERSION_INFO
    from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

    def check_supported_python_version():
        from ddtrace.vendor import debtcollector

        if PYTHON_VERSION_INFO < (3, 8):
            deprecation_message = (
                "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 3.0.0."
            )
            if PYTHON_VERSION_INFO < (3, 7):
                deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 2.0.0."
            debtcollector.deprecate(
                (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
                category=DDTraceDeprecationWarning,
            )

    check_supported_python_version()
