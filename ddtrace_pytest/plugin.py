"""Proxy for the main ddtrace pytest plugin."""

from . import _CIVISIBILITY_ENABLED


# Two-fold branching, depending on killswitch: import from actual plugin or use disabled implementation
if _CIVISIBILITY_ENABLED:
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403,F401
else:
    # Disabled plugin implementation
    def pytest_addoption(parser):
        """Add main ddtrace options even when disabled to prevent CLI errors."""
        group = parser.getgroup("ddtrace")
        group._addoption(
            "--ddtrace",
            action="store_true",
            dest="ddtrace",
            default=False,
            help="Enable tracing of pytest functions.",
        )
        group._addoption(
            "--no-ddtrace",
            action="store_true",
            dest="no-ddtrace",
            default=False,
            help="Disable tracing of pytest functions.",
        )
        group._addoption(
            "--ddtrace-patch-all",
            action="store_true",
            dest="ddtrace-patch-all",
            default=False,
            help="Call ddtrace._patch_all before running tests.",
        )
        group._addoption(
            "--ddtrace-include-class-name",
            action="store_true",
            dest="ddtrace-include-class-name",
            default=False,
            help="Prepend 'ClassName.' to names of class-based tests.",
        )
        group._addoption(
            "--ddtrace-iast-fail-tests",
            action="store_true",
            dest="ddtrace-iast-fail-tests",
            default=False,
            help="Fail tests that trigger IAST vulnerabilities.",
        )

        # Add ini options
        parser.addini("ddtrace", help="Enable tracing of pytest functions.", type="bool")
        parser.addini("no-ddtrace", help="Disable tracing of pytest functions.", type="bool")
        parser.addini("ddtrace-patch-all", help="Call ddtrace._patch_all before running tests.", type="bool")
        parser.addini(
            "ddtrace-include-class-name", help="Prepend 'ClassName.' to names of class-based tests.", type="bool"
        )

    def pytest_configure(config):
        """Add markers even when disabled."""
        config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
