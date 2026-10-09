"""
Hooks for ddtrace pytest features that are not directly related to Test Optimization (such as IAST).

This module is not installed as a pytest entry point, because we want to have a single entry point for ddtrace that can
be easily disabled by invoking pytest with `-p no:ddtrace`. As such, the functions in this file are call explicitly from
`ddtrace/testing/internal/pytest/plugin.py`.
"""

from ddtrace.internal.settings._telemetry import config as telemetry_config
from ddtrace.internal.settings.asm import config as asm_config


def pytest_addoption(parser):
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group.addoption(
        "--ddtrace-iast-fail-tests",
        action="store_true",
        dest="ddtrace-iast-fail-tests",
        default=False,
        help="If IAST is enabled, fail tests with detected vulnerabilities",
    )


def iast_fixture():
    """Return the IAST pytest fixture, for a plugin module to bind at module level so pytest collects it."""
    from ddtrace.appsec._iast._pytest_plugin import ddtrace_iast

    return ddtrace_iast


def activate_iast():
    from ddtrace.appsec._iast import _iast_pytest_activation

    _iast_pytest_activation()


def print_iast_report(terminalreporter):
    from ddtrace.appsec._iast._pytest_plugin import print_iast_report

    print_iast_report(terminalreporter)


def pytest_configure(config):
    telemetry_config.DEPENDENCY_COLLECTION = False

    if asm_config._iast_enabled:
        # Cause ddtrace_iast fixture to be loaded by pytest.
        iast_fixture()

        # Run IAST-specific routines.
        activate_iast()

    config.pluginmanager.register(DDTracePytestHooks())


class DDTracePytestHooks:
    def pytest_terminal_summary(self, terminalreporter):
        if asm_config._iast_enabled:
            print_iast_report(terminalreporter)
