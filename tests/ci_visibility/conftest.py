from __future__ import annotations

import pytest

from ddtrace.internal.ci_visibility.recorder import CIVisibility


@pytest.fixture(scope="function", autouse=True)
def _ci_visibility_isolation():
    """Suspend and resume outer CIVisibility instance around every test.

    When the ci_visibility suite runs with --ddtrace in the outer pytest session,
    CIVisibility is already enabled (outer session's instance).  Without this fixture:
    - tests that call CIVisibility.enable() receive a silent no-op (already enabled)
    - tests that call CIVisibility.disable() pop and stop the outer session's instance

    This fixture wraps every test with a suspend/resume so that:
    - the test body starts with CIVisibility disabled (clean stack)
    - the test can enable/disable its own instance freely
    - the outer session's instance is never stopped — it resumes after the test

    Unlike the old save_state/restore_state approach, _suspend() does NOT call
    stop() on the outer instance, so the outer session's tracer keeps running.
    """
    # AIDEV-NOTE: This fixture must run before the module-level _disable_ci_visibility
    # fixtures defined in individual test files. pytest runs conftest fixtures first,
    # so ordering is guaranteed.
    suspended = CIVisibility._suspend()
    yield
    try:
        if CIVisibility.enabled:
            CIVisibility.disable()
    except Exception:  # noqa: E722
        # no-dd-sa:python-best-practices/no-silent-exception
        pass
    CIVisibility._resume(suspended)
