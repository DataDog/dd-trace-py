import pytest


class TestOptHooks:
    """
    Custom hooks that can be implemented to override the module, suite, and test names reported by Test Optimization.
    """

    @pytest.hookspec(firstresult=True)
    def pytest_ddtrace_get_item_module_name(item: pytest.Item) -> str:  # type: ignore[empty-body]
        """Returns the module name to use when reporting Test Optimization results. Should be unique for each module."""

    @pytest.hookspec(firstresult=True)
    def pytest_ddtrace_get_item_suite_name(item: pytest.Item) -> str:  # type: ignore[empty-body]
        """Returns the suite name to use when reporting Test Optimization results. Should be unique for each suite."""

    @pytest.hookspec(firstresult=True)
    def pytest_ddtrace_get_item_test_name(item: pytest.Item) -> str:  # type: ignore[empty-body]
        """Returns the test name to use when reporting Test Optimization results. Should be unique for each test."""
