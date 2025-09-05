import os

from _ddtrace.internal.utils.formats import asbool


DDTRACE_HELP_MSG = "Enable tracing of pytest functions."
NO_DDTRACE_HELP_MSG = "Disable tracing of pytest functions."
DDTRACE_INCLUDE_CLASS_HELP_MSG = "Prepend 'ClassName.' to names of class-based tests."
PATCH_ALL_HELP_MSG = "Call ddtrace._patch_all before running tests."

if asbool(os.getenv("DD_CIVISIBILITY_ENABLED", "true")):
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa:F401,F403
else:
    # Avoid breaking arg parsing
    def pytest_addoption(parser):
        """Add ddtrace options."""
        group = parser.getgroup("ddtrace")

        group._addoption(
            "--ddtrace",
            action="store_true",
            dest="ddtrace",
            default=False,
            help=DDTRACE_HELP_MSG,
        )

        group._addoption(
            "--no-ddtrace",
            action="store_true",
            dest="no-ddtrace",
            default=False,
            help=NO_DDTRACE_HELP_MSG,
        )

        group._addoption(
            "--ddtrace-patch-all",
            action="store_true",
            dest="ddtrace-patch-all",
            default=False,
            help=PATCH_ALL_HELP_MSG,
        )

        group._addoption(
            "--ddtrace-include-class-name",
            action="store_true",
            dest="ddtrace-include-class-name",
            default=False,
            help=DDTRACE_INCLUDE_CLASS_HELP_MSG,
        )

        group._addoption(
            "--ddtrace-iast-fail-tests",
            action="store_true",
            dest="ddtrace-iast-fail-tests",
            default=False,
            help=DDTRACE_INCLUDE_CLASS_HELP_MSG,
        )

        parser.addini("ddtrace", DDTRACE_HELP_MSG, type="bool")
        parser.addini("no-ddtrace", DDTRACE_HELP_MSG, type="bool")
        parser.addini("ddtrace-patch-all", PATCH_ALL_HELP_MSG, type="bool")
        parser.addini("ddtrace-include-class-name", DDTRACE_INCLUDE_CLASS_HELP_MSG, type="bool")
