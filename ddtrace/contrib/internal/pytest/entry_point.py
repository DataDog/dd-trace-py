import os
import typing as t

import pytest

from ddtrace.contrib.internal.pytest.ddtestpy_interface import ddtrace_interface


if os.getenv("DD_USE_DDTESTPY", "false").lower() in ("true", "1"):

    def _is_option_true(option: str, early_config: pytest.Config, args: t.List[str]) -> bool:
        """
        Check whether a command line option is true.
        """
        return early_config.getoption(option) or early_config.getini(option) or f"--{option}" in args

    def _is_enabled_early(early_config: pytest.Config, args: t.List[str]) -> bool:
        """
        Check whether ddtrace is enabled via command line during early initialization.
        """
        if _is_option_true("no-ddtrace", early_config, args):
            return False

        return _is_option_true("ddtrace", early_config, args) or (
            tracer_interface_instance and tracer_interface_instance.should_enable_test_optimization()
        )

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_load_initial_conftests(
        early_config: pytest.Config, parser: pytest.Parser, args: t.List[str]
    ) -> t.Generator[None, None, None]:
        """
        Ensure we enable ddtrace early enough so that coverage collection is enabled by the time we collect tests.
        """
        if _is_enabled_early(early_config, args):
            ddtrace_interface._should_enable_test_optimization = True

        yield

    def pytest_addoption(parser: pytest.Parser) -> None:
        """Add ddtrace options."""
        group = parser.getgroup("ddtrace")

        group.addoption(
            "--ddtrace",
            action="store_true",
            dest="ddtrace",
            default=False,
            help="Enable Datadog Test Optimization with tracer features",
        )
        group.addoption(
            "--no-ddtrace",
            action="store_true",
            dest="no-ddtrace",
            default=False,
            help="Disable Datadog Test Optimization with tracer features (overrides --ddtrace)",
        )
        group.addoption(
            "--ddtrace-patch-all",
            action="store_true",
            dest="ddtrace-patch-all",
            default=False,
            help="Enable all tracer integrations during tests",
        )
        group.addoption(
            "--ddtrace-iast-fail-tests",
            action="store_true",
            dest="ddtrace-iast-fail-tests",
            default=False,
            help="When IAST is enabled, fail tests that have detected vulnerabilities",
        )

        parser.addini("ddtrace", "Enable Datadog Test Optimization with tracer features", type="bool")
        parser.addini(
            "no-ddtrace", "Disable Datadog Test Optimization with tracer features (overrides 'ddtrace')", type="bool"
        )
        parser.addini("ddtrace-patch-all", "Enable all tracer integrations during tests", type="bool")

    def pytest_configure(config: pytest.Config) -> None:
        yes_ddtrace = config.getoption("ddtrace") or config.getini("ddtrace")
        no_ddtrace = config.getoption("no-ddtrace") or config.getini("no-ddtrace")

        patch_all = config.getoption("ddtrace-patch-all") or config.getini("ddtrace-patch-all")

        if yes_ddtrace and not no_ddtrace:
            ddtrace_interface._should_enable_test_optimization = True

        if patch_all:
            ddtrace_interface._should_enable_trace_collection = True

else:
    from ddtrace.contrib.internal.pytest.plugin import *
