"""Discovery of the native asyncio task lists on Python 3.14+.

CI builds and runs on the same Python release, so profile-based tests pass even when the task-list offsets are wrong
for other releases. These tests check what asyncio initialization publishes to the sampler, for each order in which
asyncio, _asyncio and ddtrace can be imported. Importing ddtrace imports asyncio, so each test arranges sys.modules
before its first ddtrace import.
"""

import sys

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 14), reason="Native asyncio task lists exist on Python 3.14+")


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_neither_module_was_imported() -> None:
    import sys

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs

    assert "asyncio" not in sys.modules and "_asyncio" not in sys.modules
    messages = capture_asyncio_log()

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()
    try:
        interpreter_head, thread_head, source = _stack._asyncio_task_lists()
        assert interpreter_head > 0
        assert thread_head > 0
        assert source == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_asyncio_c_was_imported_first() -> None:
    import _asyncio  # noqa: F401, I001
    import sys

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs

    assert "_asyncio" in sys.modules and "asyncio" in sys.modules
    messages = capture_asyncio_log()

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_asyncio_c_is_imported_after_the_hooks() -> None:
    # _asyncio imports asyncio from its own init, before it sets the AsyncioDebug pointer. The asyncio hook therefore
    # runs too early and the _asyncio hook must initialize again once _asyncio has finished.
    import sys

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio
    from tests.profiling.collector.asyncio_task_lists_utils import record_init_asyncio

    # Keep the offsets unpublished until _asyncio is imported below.
    sys.modules["_asyncio"] = None  # type: ignore[assignment]
    messages = capture_asyncio_log()
    calls = record_init_asyncio()

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert _stack._asyncio_task_lists()[2] == "none"
        assert no_task_lists_logs(messages) == 1, messages

        purge_asyncio()
        calls.clear()
        import _asyncio

        # The nested asyncio hook call tried _asyncio before the AsyncioDebug pointer was set.
        assert calls == [(_asyncio, False), (_asyncio, True)], calls
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        # It saw _asyncio still initializing and did not log again.
        assert no_task_lists_logs(messages) == 1, messages
    finally:
        p.stop()


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_lists_unavailable_asyncio_c"),
    err=None,
)
def test_asyncio_task_lists_missing_when_asyncio_c_is_blocked() -> None:
    import os
    import sys
    import threading
    import time

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs

    sys.modules["_asyncio"] = None  # type: ignore[assignment]
    messages = capture_asyncio_log()

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import _asyncio
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler()
    p.start()
    try:
        tasks = _stack._asyncio_task_lists()
        assert tasks == (0, 0, "none"), tasks
        _asyncio._call_init_asyncio(sys.modules["asyncio"])
        assert no_task_lists_logs(messages) == 1, messages
        assert stack.init_asyncio([], None, None) is False

        def busy() -> None:
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                pass

        worker = threading.Thread(target=busy, name="busy_worker")
        worker.start()
        worker.join()
    finally:
        p.stop()

    # Thread stacks are still sampled without the native task lists.
    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    samples = pprof_utils.get_samples_with_label_key(profile, "thread name")
    assert "busy_worker" in pprof_utils.get_label_str_values(profile, samples, "thread name")


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_missing_when_asyncio_c_cannot_be_imported() -> None:
    # Unlike a None entry in sys.modules, a failing import never calls the _asyncio hook.
    import sys

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs

    class BlockAsyncioC:
        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            if name == "_asyncio":
                raise ImportError("blocked for this test")
            return None

    sys.meta_path.insert(0, BlockAsyncioC())
    messages = capture_asyncio_log()

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import _asyncio
    from ddtrace.profiling import profiler

    assert "_asyncio" not in sys.modules

    p = profiler.Profiler()
    p.start()
    try:
        assert _stack._asyncio_task_lists() == (0, 0, "none")
        _asyncio._call_init_asyncio(sys.modules["asyncio"])
        assert no_task_lists_logs(messages) == 1, messages
    finally:
        p.stop()


@pytest.mark.subprocess(env=dict(DD_PROFILING_STACK_ENABLED="false"), err=None)
def test_asyncio_c_hook_does_not_initialize_when_stack_is_disabled() -> None:
    import sys

    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio
    from tests.profiling.collector.asyncio_task_lists_utils import record_init_asyncio

    sys.modules["_asyncio"] = None  # type: ignore[assignment]
    calls = record_init_asyncio()

    from ddtrace.profiling import _asyncio
    from ddtrace.profiling import profiler  # noqa: F401  # registers the hooks

    purge_asyncio()
    import _asyncio as asyncio_c

    assert calls == [], calls
    # The hook still ran and kept the module.
    assert _asyncio._C_ASYNCIO is asyncio_c


@pytest.mark.subprocess(env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_lists_wall_time"), err=None)
def test_suspended_task_wall_time_is_bounded_by_elapsed_time() -> None:
    # Wrong list-head offsets made the walker find the same task many times per sample, inflating its wall time far
    # beyond the time it existed, or lose the task altogether.
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler()
    p.start()
    assert _stack._asyncio_task_lists()[2] == "runtime-table"

    async def leaf_sleep() -> None:
        await asyncio.sleep(1.0)

    async def main() -> float:
        await asyncio.sleep(0.2)
        start = time.monotonic_ns()
        await asyncio.create_task(leaf_sleep(), name="leaf_task")
        return time.monotonic_ns() - start

    elapsed_ns = asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")
    samples = [
        sample
        for sample in pprof_utils.get_samples_with_label_key(profile, "task name")
        if pprof_utils.get_str_label(profile, sample, "task name") == "leaf_task"
    ]
    assert samples, "the suspended task was not sampled"
    assert any(
        pprof_utils.get_location_from_id(profile, location_id).function_name == "leaf_sleep"
        for sample in samples
        for location_id in sample.location_id
    ), "the suspended task's coroutine frame is missing"

    task_wall_time_ns = sum(sample.value[wall_time_index] for sample in samples)
    assert task_wall_time_ns <= 1.5 * elapsed_ns, (task_wall_time_ns, elapsed_ns)


@pytest.mark.subprocess(env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_lists_handoff"), err=None)
def test_task_of_an_exited_thread_is_sampled() -> None:
    # When a thread exits, CPython moves its pending tasks to the interpreter's task list. Only that list finds the
    # leaf task here, so a wrong interpreter-list offset loses it even when the thread-list offset is right.
    import asyncio
    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler()
    p.start()
    assert _stack._asyncio_task_lists()[2] == "runtime-table"

    async def leaf_sleep() -> None:
        await asyncio.sleep(1.0)

    async def wait_for(task: asyncio.Task[None]) -> None:
        await task

    loop = asyncio.new_event_loop()
    start_ns: list[int] = []
    leaf_tasks: list[asyncio.Task[None]] = []

    def start_leaf_task() -> None:
        start_ns.append(time.monotonic_ns())
        leaf_tasks.append(loop.create_task(leaf_sleep(), name="leaf_task"))
        loop.run_until_complete(asyncio.sleep(0.05))

    thread = threading.Thread(target=start_leaf_task)
    thread.start()
    thread.join()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(wait_for(leaf_tasks[0]))
    elapsed_ns = time.monotonic_ns() - start_ns[0]
    loop.close()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")
    samples = [
        sample
        for sample in pprof_utils.get_samples_with_label_key(profile, "task name")
        if pprof_utils.get_str_label(profile, sample, "task name") == "leaf_task"
    ]
    assert samples, "the task of the exited thread was not sampled"
    assert any(
        pprof_utils.get_location_from_id(profile, location_id).function_name == "leaf_sleep"
        for sample in samples
        for location_id in sample.location_id
    ), "the task's coroutine frame is missing"

    task_wall_time_ns = sum(sample.value[wall_time_index] for sample in samples)
    assert task_wall_time_ns <= 1.5 * elapsed_ns, (task_wall_time_ns, elapsed_ns)


@pytest.mark.subprocess(err=None)
def test_completed_asyncio_c_skips_modules_still_initializing() -> None:
    import sys
    from types import ModuleType
    from types import SimpleNamespace

    from ddtrace.profiling import _asyncio

    real = sys.modules["_asyncio"]
    _asyncio._C_ASYNCIO = None

    initializing = ModuleType("_asyncio")
    initializing.__spec__ = SimpleNamespace(_initializing=True)  # type: ignore[assignment]
    sys.modules["_asyncio"] = initializing
    assert _asyncio._completed_c_asyncio() is None
    assert _asyncio._C_ASYNCIO is None

    sys.modules["_asyncio"] = None  # type: ignore[assignment]
    assert _asyncio._completed_c_asyncio() is None

    finished = ModuleType("_asyncio")
    finished.__spec__ = SimpleNamespace(_initializing=False)  # type: ignore[assignment]
    sys.modules["_asyncio"] = finished
    assert _asyncio._completed_c_asyncio() is finished
    # Cached: later changes to sys.modules do not matter.
    sys.modules["_asyncio"] = real
    assert _asyncio._completed_c_asyncio() is finished


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_failure_with_a_module_is_logged() -> None:
    # As in the _asyncio-after-the-hooks test, but discovery is made to fail: the call that passed the module must log,
    # even though importlib still reports _asyncio as initializing while its post-import hook runs.
    import sys
    from types import ModuleType

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio
    from tests.profiling.collector.asyncio_task_lists_utils import record_init_asyncio

    sys.modules["_asyncio"] = None  # type: ignore[assignment]
    messages = capture_asyncio_log()
    calls = record_init_asyncio(result=False)

    from ddtrace.profiling import _asyncio
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        purge_asyncio()
        _asyncio._LOGGED_NO_TASK_LISTS = False
        messages.clear()
        calls.clear()

        logs_per_call = []
        real_call_init_asyncio = _asyncio._call_init_asyncio

        def call_init_asyncio(asyncio: ModuleType) -> None:
            real_call_init_asyncio(asyncio)
            logs_per_call.append(no_task_lists_logs(messages))

        _asyncio._call_init_asyncio = call_init_asyncio
        import _asyncio as asyncio_c

        assert [module for module, _ in calls] == [asyncio_c, asyncio_c], calls
        # Nothing after the nested asyncio hook call, made while _asyncio was executing; one log after the _asyncio
        # hook call.
        assert logs_per_call == [0, 1], logs_per_call
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_profiler_starts_inside_asyncio_c_hook() -> None:
    # Starting the profiler from a hook on _asyncio registers ddtrace's hooks while importlib still reports _asyncio as
    # initializing, after its init has finished.
    import sys

    from ddtrace.internal.module import ModuleWatchdog
    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio

    assert "ddtrace.profiling" not in sys.modules
    messages = capture_asyncio_log()
    purge_asyncio()

    started = []

    def start_profiling(module: object) -> None:
        from ddtrace.profiling import profiler

        p = profiler.Profiler()
        p.start()
        # Asserted outside the hook, as the import swallows exceptions from hooks.
        started.append((p, module.__spec__._initializing))  # type: ignore[attr-defined]

    ModuleWatchdog.register_module_hook("_asyncio", start_profiling)
    import _asyncio  # noqa: F401

    from ddtrace.internal.datadog.profiling.stack import _stack

    [(p, initializing)] = started
    try:
        assert initializing
        # No event loop is needed.
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_profiling_is_imported_inside_asyncio_c_hook() -> None:
    # As in test_asyncio_task_lists_found_when_profiler_starts_inside_asyncio_c_hook, but the profiler starts only after
    # _asyncio has finished initializing.
    import sys

    from ddtrace.internal.module import ModuleWatchdog
    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio

    assert "ddtrace.profiling" not in sys.modules
    messages = capture_asyncio_log()
    purge_asyncio()

    imported = []

    def import_profiling(module: object) -> None:
        from ddtrace.profiling import profiler  # noqa: F401

        # Asserted outside the hook, as the import swallows exceptions from hooks.
        imported.append(module.__spec__._initializing)  # type: ignore[attr-defined]

    ModuleWatchdog.register_module_hook("_asyncio", import_profiling)
    import _asyncio  # noqa: F401

    assert imported == [True], imported
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_profiler_starts_inside_a_later_watchdog_asyncio_c_hook() -> None:
    # The hooks of a second watchdog run after those of the first one, which then has no more hooks to call for
    # _asyncio, while importlib still reports _asyncio as initializing.
    import sys

    from ddtrace.internal.module import ModuleWatchdog
    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio

    assert "ddtrace.profiling" not in sys.modules
    messages = capture_asyncio_log()
    purge_asyncio()

    started = []

    class LaterWatchdog(ModuleWatchdog):
        def after_import(self, module):
            if module.__name__ == "_asyncio":
                from ddtrace.profiling import profiler

                p = profiler.Profiler()
                p.start()
                started.append((p, module.__spec__._initializing))
            super().after_import(module)

    ModuleWatchdog.install()
    LaterWatchdog.install()
    try:
        import _asyncio  # noqa: F401

        from ddtrace.internal.datadog.profiling.stack import _stack

        [(p, initializing)] = started
        try:
            assert initializing
            assert _stack._asyncio_task_lists()[2] == "runtime-table"
            assert no_task_lists_logs(messages) == 0, messages
        finally:
            p.stop()
    finally:
        LaterWatchdog.uninstall()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_profiling_is_imported_in_another_thread_inside_asyncio_c_hook() -> None:
    # Another thread registers ddtrace's _asyncio hook while the importing thread is calling the hooks of _asyncio, so
    # registration calls it, on the other thread, with _asyncio reported as initializing.
    import sys
    import threading

    from ddtrace.internal.module import ModuleWatchdog
    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio

    assert "ddtrace.profiling" not in sys.modules
    messages = capture_asyncio_log()
    purge_asyncio()

    errors = []
    imported = []

    def import_profiling():
        try:
            from ddtrace.profiling import profiler  # noqa: F401

            imported.append(sys.modules["_asyncio"].__spec__._initializing)
        except BaseException as e:
            errors.append(repr(e))

    def hook(module):
        worker = threading.Thread(target=import_profiling)
        worker.start()
        worker.join(10)
        if worker.is_alive():
            errors.append("the import of the profiler did not finish")

    ModuleWatchdog.register_module_hook("_asyncio", hook)
    import _asyncio  # noqa: F401

    assert not errors, errors
    # Asserted outside the hook, as the import swallows exceptions from hooks.
    assert imported == [True], imported

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_found_when_profiler_starts_while_another_thread_executes_asyncio_c() -> None:
    # Another thread started importing _asyncio before ddtrace was imported, so no ddtrace hook runs once that import
    # finishes. The profiler starts while _asyncio is still executing, before it has set its AsyncioDebug pointer, so
    # discovery fails then and succeeds once an event loop is set.
    import sys
    import threading
    import weakref

    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs

    assert "asyncio" not in sys.modules and "_asyncio" not in sys.modules and "ddtrace" not in sys.modules
    messages = capture_asyncio_log()

    entered = threading.Event()
    release = threading.Event()
    errors = []
    real_weak_set = weakref.WeakSet

    class PausingWeakSet(real_weak_set):  # type: ignore[valid-type,misc]
        # _asyncio's init creates a WeakSet after importing asyncio, shortly before it sets its AsyncioDebug pointer.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            asyncio = sys.modules.get("asyncio")
            if (
                threading.current_thread() is importer
                and asyncio is not None
                and not asyncio.__spec__._initializing
                and not entered.is_set()
            ):
                entered.set()
                if not release.wait(10):
                    errors.append("_asyncio was not released")

    def import_asyncio_c():
        try:
            import _asyncio  # noqa: F401
        except BaseException as e:
            errors.append(repr(e))

    importer = threading.Thread(target=import_asyncio_c)
    weakref.WeakSet = PausingWeakSet  # type: ignore[misc]
    importer.start()
    try:
        assert entered.wait(10), "_asyncio did not create its WeakSet"

        from ddtrace.internal.datadog.profiling.stack import _stack
        from ddtrace.profiling import profiler

        p = profiler.Profiler()
        p.start()
        source_while_executing = _stack._asyncio_task_lists()[2]
    finally:
        release.set()
        importer.join(10)
        weakref.WeakSet = real_weak_set  # type: ignore[misc]
    try:
        assert not importer.is_alive()
        assert not errors, errors
        assert source_while_executing == "none"

        import asyncio

        asyncio.run(asyncio.sleep(0))
        assert _stack._asyncio_task_lists()[2] == "runtime-table"
        assert no_task_lists_logs(messages) == 0, messages
    finally:
        p.stop()


@pytest.mark.subprocess(err=None)
def test_asyncio_task_lists_failure_when_profiler_starts_inside_asyncio_c_hook_is_logged_once_a_loop_is_set() -> None:
    # As in the start-inside-the-hook test, but discovery is made to fail. Both calls see _asyncio initializing, so
    # neither logs, and no later import hook call follows. Setting an event loop tries again, and logs once.
    import sys

    from ddtrace.internal.module import ModuleWatchdog
    from tests.profiling.collector.asyncio_task_lists_utils import capture_asyncio_log
    from tests.profiling.collector.asyncio_task_lists_utils import no_task_lists_logs
    from tests.profiling.collector.asyncio_task_lists_utils import purge_asyncio
    from tests.profiling.collector.asyncio_task_lists_utils import record_init_asyncio

    assert "ddtrace.profiling" not in sys.modules
    messages = capture_asyncio_log()
    calls = record_init_asyncio(result=False)
    purge_asyncio()

    started = []

    def start_profiling(module: object) -> None:
        from ddtrace.profiling import profiler

        p = profiler.Profiler()
        p.start()
        # Asserted outside the hook, as the import swallows exceptions from hooks.
        started.append(p)

    ModuleWatchdog.register_module_hook("_asyncio", start_profiling)
    import _asyncio

    [p] = started
    try:
        assert [module for module, _ in calls] == [_asyncio, _asyncio], calls
        assert no_task_lists_logs(messages) == 0, messages

        import asyncio

        asyncio.run(asyncio.sleep(0))
        assert [module for module, _ in calls] == [_asyncio] * 3, calls
        assert no_task_lists_logs(messages) == 1, messages

        # Native code has had the finished module, so later loops do not try again.
        asyncio.run(asyncio.sleep(0))
        assert len(calls) == 3, calls
    finally:
        p.stop()
