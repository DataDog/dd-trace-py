# DEPRECATED: This module is scheduled for removal in dd-trace-py 5.0.0.
# Use DD_PYTEST_USE_NEW_PLUGIN=true (or unset; it is now the default) to opt into
# the new plugin at ddtrace/testing/internal/pytest/.
"""pytest-ddtrace hooks.

These hooks are used to provide extra data used by the Datadog CI Visibility plugin.

For example: module, suite, and test names for a given item.

Note that these names will affect th display and reporting of tests in the Datadog UI, as well as information stored
the Intelligent Test Runner. Differing hook implementations may impact the behavior of Datadog CI Visibility products.
"""

import pytest


@pytest.hookspec(firstresult=True)
def pytest_ddtrace_get_item_module_name(item: pytest.Item) -> str:
    """Returns the module name to use when reporting CI Visibility results, should be unique"""


@pytest.hookspec(firstresult=True)
def pytest_ddtrace_get_item_suite_name(item: pytest.Item) -> str:
    """Returns the suite name to use when reporting CI Visibility result, should be unique"""


@pytest.hookspec(firstresult=True)
def pytest_ddtrace_get_item_test_name(item: pytest.Item) -> str:
    """Returns the test name to use when reporting CI Visibility result, should be unique"""
