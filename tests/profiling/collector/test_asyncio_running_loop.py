"""The stack profiler renders the tasks of the asyncio loop that runs on a thread, even if it was never configured.

Each thread record has two loop slots. The configured loop is the one set_event_loop, uvloop.new_event_loop, or late
attachment before Python 3.13 records. The running loop is the plain asyncio loop (a BaseEventLoop) whose run_forever
runs on the thread right now. The sampler reads the running loop when one is set, otherwise the configured loop.
These tests read both slots through the test-only _stack._asyncio_loop_slots getter, wrapped by asyncio_loop_slots.
"""

import sys
from typing import Any

import pytest

from tests.profiling.collector.test_utils import uvloop_available


# Each test takes a few seconds. The bound turns a hang, such as a loop that never stops, into a failure.
SUBPROCESS_TIMEOUT_SECONDS = 60


def asyncio_loop_slots(thread_id: int) -> dict[str, Any]:
    """Return the loop slots of a registered thread. Fails if the thread has no native record."""
    from ddtrace.internal.datadog.profiling.stack import _stack

    slots = _stack._asyncio_loop_slots(thread_id)
    assert slots is not None, "thread %d is not registered" % thread_id
    return slots


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.Runner was added in Python 3.11")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_runner_loop_factory_tasks_are_rendered_as_tasks"),
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_asyncio_runner_loop_factory_tasks_are_rendered_as_tasks() -> None:
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def worker() -> None:
        end = time.monotonic() + 0.75
        while time.monotonic() < end:
            await asyncio.sleep(0.05)

    async def main() -> None:
        await asyncio.gather(
            asyncio.create_task(worker(), name="worker 1"), asyncio.create_task(worker(), name="worker 2")
        )

    p = profiler.Profiler()
    p.start()
    # With a loop_factory, Runner never calls set_event_loop, so the loop is never configured.
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:  # type: ignore[attr-defined]
        runner.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    task_names = set(
        pprof_utils.get_label_str_values(
            profile, pprof_utils.get_samples_with_label_key(profile, "task name"), "task name"
        )
    )
    assert {"worker 1", "worker 2"} <= task_names, task_names


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_bare_run_until_complete_tasks_are_rendered_as_tasks"),
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_asyncio_bare_run_until_complete_tasks_are_rendered_as_tasks() -> None:
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def worker() -> None:
        end = time.monotonic() + 0.75
        while time.monotonic() < end:
            await asyncio.sleep(0.05)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        await asyncio.gather(loop.create_task(worker()), loop.create_task(worker()))

    p = profiler.Profiler()
    p.start()
    # new_event_loop does not call set_event_loop, so the loop is never configured.
    loop = asyncio.new_event_loop()
    loop.run_until_complete(main())
    loop.close()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    task_names = pprof_utils.get_label_str_values(
        profile, pprof_utils.get_samples_with_label_key(profile, "task name"), "task name"
    )
    assert any(name.startswith("Task-") for name in task_names), task_names


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_nested_run_forever_keeps_the_outer_running_loop() -> None:
    import asyncio
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()
    outer = asyncio.new_event_loop()
    inner = asyncio.new_event_loop()

    async def main() -> None:
        assert asyncio_loop_slots(thread_id)["running_loop"] == id(outer)
        try:
            inner.run_forever()
        except RuntimeError:
            pass
        else:
            raise AssertionError("a nested run_forever must raise")
        # The rejected run did not replace or clear the outer loop.
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == id(outer), slots
        assert slots["effective_loop"] == id(outer), slots

    p = profiler.Profiler()
    p.start()
    outer.run_until_complete(main())
    slots = asyncio_loop_slots(thread_id)
    assert slots["running_loop"] == 0, slots
    p.stop()


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_set_event_loop_registration_survives_run_until_complete() -> None:
    import asyncio
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()
    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def noop() -> None:
        pass

    loop.run_until_complete(noop())
    slots = asyncio_loop_slots(thread_id)
    assert slots["configured_loop"] == id(loop), slots
    assert slots["running_loop"] == 0, slots
    assert slots["effective_loop"] == id(loop), slots
    p.stop()


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_set_event_loop_during_a_run_keeps_the_sampler_on_the_running_loop() -> None:
    import asyncio
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def main() -> None:
        asyncio.set_event_loop(loop_b)
        slots = asyncio_loop_slots(thread_id)
        assert slots["configured_loop"] == id(loop_b), slots
        assert slots["running_loop"] == id(loop_a), slots
        assert slots["effective_loop"] == id(loop_a), slots

    p = profiler.Profiler()
    p.start()
    loop_a.run_until_complete(main())
    slots = asyncio_loop_slots(thread_id)
    assert slots["running_loop"] == 0, slots
    assert slots["effective_loop"] == id(loop_b), slots
    p.stop()


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_register_thread_on_a_live_thread_keeps_the_running_loop() -> None:
    import asyncio
    import threading

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()
    loop = asyncio.new_event_loop()

    async def main() -> None:
        # Profiler start registers living threads again, which replaces their records.
        _stack.register_thread(thread_id, threading.get_native_id(), threading.current_thread().name)
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == id(loop), slots

    p = profiler.Profiler()
    p.start()
    loop.run_until_complete(main())
    p.stop()


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_loop_that_stops_while_profiling_is_stopped_leaves_no_running_loop() -> None:
    import asyncio
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()

    async def stop_profiler(p: profiler.Profiler) -> None:
        assert asyncio_loop_slots(thread_id)["running_loop"] == id(asyncio.get_running_loop())
        p.stop()

    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(stop_profiler(p))
    # The thread record outlives the stop, and the hooks cleared the loop that stopped.
    slots = asyncio_loop_slots(thread_id)
    assert slots is not None
    assert slots["running_loop"] == 0, slots

    # A loop that both starts and stops while profiling is stopped is cleared too.
    async def check_running() -> None:
        assert asyncio_loop_slots(thread_id)["running_loop"] == id(asyncio.get_running_loop())

    loop.run_until_complete(check_running())
    assert asyncio_loop_slots(thread_id)["running_loop"] == 0


@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_fork_inside_a_running_loop_leaves_the_child_without_loops() -> None:
    import asyncio
    import os
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    async def main() -> None:
        assert asyncio_loop_slots(threading.get_ident())["running_loop"] == id(asyncio.get_running_loop())
        pid = os.fork()
        if pid == 0:
            # The child rebuilds thread records without loops: the forking thread still has a record (the helper fails
            # if it does not), but no running or configured loop, so it renders as a plain thread. The child must leave
            # through os._exit, so a failed check exits with 1 instead of running the parent's code.
            exit_code = 1
            try:
                slots = asyncio_loop_slots(threading.get_ident())
                exit_code = 0 if slots["running_loop"] == 0 and slots["effective_loop"] == 0 else 1
            finally:
                os._exit(exit_code)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status

    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(main())
    p.stop()


@pytest.mark.skipif(sys.version_info < (3, 13), reason="_run_forever_setup and _run_forever_cleanup were added in 3.13")
@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_custom_loop_calling_run_forever_setup_directly_is_registered() -> None:
    import asyncio
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    thread_id = threading.get_ident()
    p = profiler.Profiler()
    p.start()

    # A GUI integration drives the loop itself instead of calling run_forever.
    loop = asyncio.new_event_loop()
    loop._run_forever_setup()  # type: ignore[attr-defined]
    try:
        # With a callback ready, _run_once polls without blocking.
        loop.call_soon(lambda: None)
        loop._run_once()  # type: ignore[attr-defined]
        assert asyncio_loop_slots(thread_id)["running_loop"] == id(loop)
    finally:
        loop._run_forever_cleanup()  # type: ignore[attr-defined]
    assert asyncio_loop_slots(thread_id)["running_loop"] == 0
    loop.close()
    p.stop()


@pytest.mark.skipif(sys.version_info < (3, 13), reason="late attachment uses the running-loop slot on 3.13+ only")
@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_profiler_started_inside_a_running_loop_reads_it_and_keeps_the_configured_loop() -> None:
    import asyncio
    import threading
    from typing import Any

    thread_id = threading.get_ident()
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def main() -> Any:
        # The thread is registered only now, after A started, so no hook could record A. Only late attachment can.
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == id(loop_a), slots
        # Late attachment leaves the configured slot alone.
        assert slots["configured_loop"] == 0, slots

        asyncio.set_event_loop(loop_b)
        slots = asyncio_loop_slots(thread_id)
        assert slots["configured_loop"] == id(loop_b), slots
        assert slots["effective_loop"] == id(loop_a), slots

        # A nested run of another loop is rejected and leaves A in the running slot.
        try:
            loop_b.run_forever()
        except RuntimeError:
            pass
        else:
            raise AssertionError("a nested run_forever must raise")
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == id(loop_a), slots
        return p

    p = loop_a.run_until_complete(main())

    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    # A started before the hooks existed, but its run_forever still calls the wrapped cleanup, which clears A.
    slots = asyncio_loop_slots(thread_id)
    assert slots["running_loop"] == 0, slots
    assert slots["configured_loop"] == id(loop_b), slots
    assert slots["effective_loop"] == id(loop_b), slots
    p.stop()


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="late attachment uses the configured slot before 3.13")
@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_late_attachment_before_3_13_uses_the_configured_slot() -> None:
    import asyncio
    import threading
    from typing import Any

    thread_id = threading.get_ident()
    loop_a = asyncio.new_event_loop()
    loop_c = asyncio.new_event_loop()

    async def main() -> Any:
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        # A's run_forever frame started with the original code, so nothing would clear a running-loop entry for A.
        slots = asyncio_loop_slots(thread_id)
        assert slots["configured_loop"] == id(loop_a), slots
        assert slots["running_loop"] == 0, slots
        assert slots["effective_loop"] == id(loop_a), slots
        assert slots["effective_uvloop"] is False, slots
        return p

    p = loop_a.run_until_complete(main())

    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    # A later run on the same thread goes through the hook and wins over the configured slot.
    async def check_running() -> None:
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == id(loop_c), slots
        assert slots["effective_loop"] == id(loop_c), slots
        assert slots["effective_uvloop"] is False, slots

    loop_c.run_until_complete(check_running())
    assert asyncio_loop_slots(thread_id)["running_loop"] == 0

    try:
        import uvloop
    except ImportError:
        uvloop = None
    if uvloop is not None:
        # uvloop is imported after ddtrace, so creating a uvloop loop makes it the configured loop with uvloop mode on.
        # A later uvloop run on the same thread is then read with uvloop mode.
        uv_loop = uvloop.new_event_loop()

        async def check_uvloop_run() -> None:
            slots = asyncio_loop_slots(thread_id)
            assert slots["running_loop"] == 0, slots
            assert slots["effective_loop"] == id(uv_loop), slots
            assert slots["effective_uvloop"] is True, slots

        uv_loop.run_until_complete(check_uvloop_run())
        uv_loop.close()
    p.stop()


@pytest.mark.skipif(sys.version_info < (3, 13), reason="late attachment uses the running-loop slot on 3.13+ only")
@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_profiler_started_inside_a_loop_that_overrides_run_forever_uses_the_configured_slot() -> None:
    import asyncio
    import threading
    from typing import Any

    class LoopWithOwnRunForever(asyncio.SelectorEventLoop):
        # Stands in for a third-party loop that overrides run_forever. Such an override may never call
        # _run_forever_cleanup, so profiler start cannot rely on cleanup to clear a running-loop entry. This one
        # calls the base method only so that the test loop runs.
        def run_forever(self) -> None:
            super().run_forever()

    thread_id = threading.get_ident()
    loop = LoopWithOwnRunForever()

    async def main() -> Any:
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        # The loop goes into the configured slot, as before this change, so a later set_event_loop can replace it.
        slots = asyncio_loop_slots(thread_id)
        assert slots["running_loop"] == 0, slots
        assert slots["configured_loop"] == id(loop), slots
        assert slots["effective_loop"] == id(loop), slots
        return p

    p = loop.run_until_complete(main())
    loop.close()
    p.stop()


@pytest.mark.skipif(not uvloop_available(), reason="uvloop not available")
@pytest.mark.subprocess(err=None, timeout=SUBPROCESS_TIMEOUT_SECONDS)
def test_asyncio_late_attachment_to_uvloop_then_an_asyncio_run_turns_uvloop_mode_off() -> None:
    import asyncio
    import threading
    from typing import Any

    import uvloop

    thread_id = threading.get_ident()

    async def attach() -> Any:
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        slots = asyncio_loop_slots(thread_id)
        assert slots["configured_loop"] == id(asyncio.get_running_loop()), slots
        assert slots["effective_uvloop"] is True, slots
        return p

    loop = uvloop.new_event_loop()
    p = loop.run_until_complete(attach())

    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    # A plain asyncio run on the same thread is read with uvloop mode off, although the configured loop is uvloop.
    async def check_running() -> None:
        slots = asyncio_loop_slots(thread_id)
        assert slots["effective_loop"] == id(asyncio.get_running_loop()), slots
        assert slots["using_uvloop"] is True, slots
        assert slots["effective_uvloop"] is False, slots

    asyncio_loop = asyncio.new_event_loop()
    asyncio_loop.run_until_complete(check_running())
    p.stop()


@pytest.mark.skipif(not uvloop_available(), reason="uvloop not available")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_STACK_UVLOOP="false"),
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_asyncio_late_attachment_to_uvloop_with_uvloop_support_off_keeps_uvloop_mode_off() -> None:
    import asyncio
    import threading
    from typing import Any

    import uvloop

    thread_id = threading.get_ident()

    async def attach() -> Any:
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        # The loop is still attached, but turning uvloop support off must also keep uvloop unwinding off.
        slots = asyncio_loop_slots(thread_id)
        assert slots["configured_loop"] == id(asyncio.get_running_loop()), slots
        assert slots["using_uvloop"] is False, slots
        assert slots["effective_uvloop"] is False, slots
        return p

    loop = uvloop.new_event_loop()
    p = loop.run_until_complete(attach())
    loop.close()
    p.stop()


@pytest.mark.skipif(not uvloop_available(), reason="uvloop not available")
@pytest.mark.skipif(sys.version_info < (3, 11), reason="uvloop.run uses asyncio.Runner from Python 3.11")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_profiler_started_inside_uvloop_run_then_second_uvloop_run"),
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_asyncio_profiler_started_inside_uvloop_run_then_second_uvloop_run_uses_uvloop_mode() -> None:
    import asyncio
    import os
    import threading
    import time
    from typing import Any

    import uvloop

    thread_id = threading.get_ident()

    async def attach() -> Any:
        from ddtrace.profiling import profiler
        from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

        p = profiler.Profiler()
        p.start()
        assert asyncio_loop_slots(thread_id)["effective_uvloop"] is True
        return p

    p = uvloop.run(attach())

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots

    async def worker() -> None:
        end = time.monotonic() + 0.75
        while time.monotonic() < end:
            await asyncio.sleep(0.05)

    async def second_run() -> None:
        slots = asyncio_loop_slots(thread_id)
        assert slots["effective_loop"] == id(asyncio.get_running_loop()), slots
        assert slots["effective_uvloop"] is True, slots
        await asyncio.create_task(worker(), name="second run worker")

    uvloop.run(second_run())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    task_names = pprof_utils.get_label_str_values(
        profile, pprof_utils.get_samples_with_label_key(profile, "task name"), "task name"
    )
    assert "second run worker" in task_names, set(task_names)
