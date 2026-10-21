import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Native asyncio task lists exist on Python 3.14+")
@pytest.mark.subprocess(err=None)
def test_asyncio_init_locates_native_task_lists() -> None:
    # Task-set discovery still finds tasks when native list discovery fails, so profile-based tests cannot detect a
    # discovery regression. Check what asyncio initialization publishes to the sampler instead.
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()
    try:
        import asyncio  # noqa: F401

        interpreter_head, thread_head, source = _stack._asyncio_task_lists()
        assert interpreter_head > 0
        assert thread_head > 0
        # CI builds and runs on the same patch release, where the compile-time fallback would hide a discovery failure.
        assert source == "runtime-table"
    finally:
        p.stop()
