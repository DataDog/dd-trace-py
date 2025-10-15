from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


class DDTraceHooks:
    """
    Pytest initialization and hooks for integration with ddtestpy.

    When DD_USE_DDTESTPY is true, the ddtestpy pytest plugin is used instead of the native ddtrace pytest plugin. In
    that case, when the `--ddtrace` option is used, `ddtestpy`_ will try to import and register this class as a pytest
    plugin. This allows us to perform any pytest initialization and hooks for products other than Test Optimization
    (such as IAST), but only if `--ddtrace` is used.

    .. _ddtestpy: https://github.com/DataDog/dd-test-py/blob/main/ddtestpy/ddtrace_integration/pytest_entry_point.py
    """

    def __init__(self):
        pass

    def pytest_configure(self):
        if asm_config._iast_enabled:
            # Cause IAST fixture to be loaded.
            from ddtrace.appsec._iast import _iast_pytest_activation
            from ddtrace.appsec._iast._pytest_plugin import ddtrace_iast  # noqa:F401

            _iast_pytest_activation()

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        try:
            if asm_config._iast_enabled:
                from ddtrace.appsec._iast._pytest_plugin import print_iast_report

                print_iast_report(terminalreporter)
        except Exception:  # noqa: E722
            log.debug("Encountered error during code security summary", exc_info=True)
