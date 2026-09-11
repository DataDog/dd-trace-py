import pytest


@pytest.fixture(autouse=True)
def _isolate_coverage_patch_state():
    """Save and restore patch module state around every test in this package.

    Two things must be isolated:
    1. The patch module's own globals (_coverage_instance etc.) so tests that
       call start_coverage / stop_coverage don't leak into each other.
    2. Coverage._instances -- the class-level stack that Coverage.current() reads.
       pytest-cov pushes its own instance there for the whole session, which would
       make is_coverage_running() return True even before the test calls start_coverage.
       We clear that stack for the duration of each test and restore it afterwards.
    """
    from coverage import Coverage

    import ddtrace.contrib.internal.coverage.patch as p

    # Save patch module state.
    saved_instance = p._coverage_instance
    saved_owned = p._owns_coverage_instance
    saved_pct = p._cached_coverage_percentage

    # Save and clear Coverage._instances so Coverage.current() returns None
    # inside the test (hides the outer pytest-cov instance).
    saved_cv_instances: list = []
    if hasattr(Coverage, "_instances"):
        saved_cv_instances = list(Coverage._instances)
        Coverage._instances.clear()

    # Start each test with a blank slate.
    p._coverage_instance = None
    p._owns_coverage_instance = False
    p._cached_coverage_percentage = None

    yield

    # If the test left an owned instance running, stop and erase it so it
    # doesn't write .coverage files that confuse the outer coverage combine step.
    if p._coverage_instance is not None and p._owns_coverage_instance:
        try:
            p._coverage_instance.stop()
        except Exception:
            pass
        try:
            p._coverage_instance.erase()
        except Exception:
            pass

    # Restore Coverage._instances so the outer pytest-cov session continues normally.
    if hasattr(Coverage, "_instances"):
        Coverage._instances.clear()
        Coverage._instances.extend(saved_cv_instances)

    # Restore patch module state.
    p._coverage_instance = saved_instance
    p._owns_coverage_instance = saved_owned
    p._cached_coverage_percentage = saved_pct
