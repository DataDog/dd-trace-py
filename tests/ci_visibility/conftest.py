from __future__ import annotations

import pytest

from ddtrace.internal.ci_visibility.recorder import CIVisibility


@pytest.fixture(scope="function", autouse=True)
def _ci_visibility_isolation():
    """Save and restore outer CIVisibility singleton state around every test.

    When the ci_visibility suite runs with --ddtrace in the outer pytest session,
    CIVisibility is already enabled (outer session's instance). Without this fixture,
    tests that call CIVisibility.enable() receive a silent no-op (instance already exists)
    and tests that call CIVisibility.disable() destroy the outer session's singleton.

    This fixture wraps every test with a save/restore so that:
    - the test body starts with CIVisibility disabled (clean slate)
    - the test can enable/disable its own instance freely
    - the outer session's instance is restored on teardown

    The existing per-file _disable_ci_visibility fixtures remain harmless: their
    pre-yield disable() is a no-op (we already disabled here), and their post-yield
    disable() cleans up whatever the test left enabled before we restore.
    """
    # AIDEV-NOTE: This fixture must run before the module-level _disable_ci_visibility
    # fixtures defined in individual test files. pytest runs conftest fixtures first,
    # so ordering is guaranteed.
    state = CIVisibility._save_state()
    try:
        if CIVisibility.enabled:
            CIVisibility.disable()
    except Exception:  # noqa: E722
        # no-dd-sa:python-best-practices/no-silent-exception
        pass
    yield
    try:
        if CIVisibility.enabled:
            CIVisibility.disable()
    except Exception:  # noqa: E722
        # no-dd-sa:python-best-practices/no-silent-exception
        pass
    CIVisibility._restore_state(state)
