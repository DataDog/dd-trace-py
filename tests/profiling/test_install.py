"""
Contracts for when the stack profiler hooks get installed, depending on DD_PROFILING_INSTALL.

The various cases are combinations of DD_PROFILING_ENABLED, DD_PROFILING_INSTALL and
DD_PROFILING_STACK values.
"""

import importlib.util
import os
import sys

import pytest

from ddtrace.internal.datadog.profiling import stack as _stack_ext


# Python 3.11.9 to 3.12.4 are not compatible with gevent, see tests/profiling/collector/test_stack.py
GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="stack profiler is not available on Windows"),
    pytest.mark.skipif(not _stack_ext.is_available, reason="stack native extension not available"),
]


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1", DD_PROFILING_INSTALL="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_enabled_installs_at_startup() -> None:
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = profiling_threading._installed_thread_hooks
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
    assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
    assert ddtrace_threading.Thread._bootstrap_inner is hooks["_bootstrap_inner"]  # type: ignore[attr-defined]
    assert "ddtrace.profiling._faulthandler" in sys.modules
    assert _task._gevent_support_initialized  # type: ignore[attr-defined]

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_disabled_installs_nothing() -> None:
    import sys

    assert "ddtrace.profiling._faulthandler" not in sys.modules

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    assert profiling_threading._installed_thread_hooks == {}
    set_native_id = ddtrace_threading.Thread._set_native_id  # type: ignore[attr-defined]
    bootstrap_inner = ddtrace_threading.Thread._bootstrap_inner  # type: ignore[attr-defined]
    assert set_native_id.__module__ != profiling_threading.__name__
    assert bootstrap_inner.__module__ != profiling_threading.__name__
    assert not _task._gevent_support_initialized  # type: ignore[attr-defined]

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_disabled_installs_on_profiler_start() -> None:
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling import Profiler
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    assert profiling_threading._installed_thread_hooks == {}
    assert not _task._gevent_support_initialized  # type: ignore[attr-defined]

    p = Profiler()
    p.start()
    try:
        hooks = profiling_threading._installed_thread_hooks
        assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
        assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
        assert ddtrace_threading.Thread._bootstrap_inner is hooks["_bootstrap_inner"]  # type: ignore[attr-defined]
        assert "ddtrace.profiling._faulthandler" in sys.modules
        assert _task._gevent_support_initialized  # type: ignore[attr-defined]
    finally:
        p.stop(flush=False)

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1", DD_PROFILING_INSTALL="1"),
    out="OK\n",
    err=None,
)
def test_install_enabled_profiling_enabled_installs_at_startup() -> None:
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = profiling_threading._installed_thread_hooks
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
    assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
    assert ddtrace_threading.Thread._bootstrap_inner is hooks["_bootstrap_inner"]  # type: ignore[attr-defined]
    assert "ddtrace.profiling._faulthandler" in sys.modules
    assert _task._gevent_support_initialized  # type: ignore[attr-defined]

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="1"),
    out="OK\n",
    err=None,
)
def test_install_enabled_profiling_disabled_installs_at_startup() -> None:
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = profiling_threading._installed_thread_hooks
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
    assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
    assert ddtrace_threading.Thread._bootstrap_inner is hooks["_bootstrap_inner"]  # type: ignore[attr-defined]
    assert "ddtrace.profiling._faulthandler" in sys.modules
    assert _task._gevent_support_initialized  # type: ignore[attr-defined]

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1", DD_PROFILING_INSTALL="0", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_enabled_stack_disabled_installs_nothing() -> None:
    import asyncio

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling.collector import threading as profiling_threading

    assert profiling_threading._installed_thread_hooks == {}
    set_native_id = ddtrace_threading.Thread._set_native_id  # type: ignore[attr-defined]
    assert set_native_id.__module__ != profiling_threading.__name__
    assert not is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]

    try:
        import gevent  # noqa: F401
    except ImportError:
        pass
    else:
        from ddtrace.profiling import _gevent

        assert not _gevent._greenlet_tracer_enabled

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="1", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_enabled_profiling_disabled_stack_disabled_installs_at_startup() -> None:
    import asyncio
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = profiling_threading._installed_thread_hooks
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
    assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
    assert "ddtrace.profiling._faulthandler" in sys.modules
    assert is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]

    try:
        import gevent  # noqa: F401
    except ImportError:
        pass
    else:
        from ddtrace.profiling import _gevent

        assert _gevent._greenlet_tracer_enabled

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1", DD_PROFILING_INSTALL="1", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_enabled_profiling_enabled_stack_disabled_installs_at_startup() -> None:
    import asyncio
    import sys

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = profiling_threading._installed_thread_hooks
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks
    assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
    assert "ddtrace.profiling._faulthandler" in sys.modules
    assert is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]

    try:
        import gevent  # noqa: F401
    except ImportError:
        pass
    else:
        from ddtrace.profiling import _gevent

        assert _gevent._greenlet_tracer_enabled

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="0", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_disabled_stack_disabled_installs_nothing() -> None:
    import sys

    assert "ddtrace.profiling._faulthandler" not in sys.modules

    import asyncio

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling.collector import _task
    from ddtrace.profiling.collector import threading as profiling_threading

    assert profiling_threading._installed_thread_hooks == {}
    set_native_id = ddtrace_threading.Thread._set_native_id  # type: ignore[attr-defined]
    assert set_native_id.__module__ != profiling_threading.__name__
    assert not is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]
    assert not _task._gevent_support_initialized  # type: ignore[attr-defined]

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1", DD_PROFILING_INSTALL="0", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_enabled_stack_disabled_installs_nothing_on_profiler_restart() -> None:
    import asyncio

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import Profiler
    from ddtrace.profiling import bootstrap
    from ddtrace.profiling.collector import threading as profiling_threading

    bootstrap.profiler.stop(flush=False)  # type: ignore[attr-defined]

    p = Profiler()
    p.start()
    try:
        assert profiling_threading._installed_thread_hooks == {}
        set_native_id = ddtrace_threading.Thread._set_native_id  # type: ignore[attr-defined]
        assert set_native_id.__module__ != profiling_threading.__name__
        assert not is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]

        try:
            import gevent  # noqa: F401
        except ImportError:
            pass
        else:
            from ddtrace.profiling import _gevent

            assert not _gevent._greenlet_tracer_enabled
    finally:
        p.stop(flush=False)

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="0", DD_PROFILING_STACK_ENABLED="0"),
    out="OK\n",
    err=None,
)
def test_install_disabled_profiling_disabled_stack_disabled_installs_nothing_on_profiler_start() -> None:
    import asyncio

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import Profiler
    from ddtrace.profiling.collector import threading as profiling_threading

    p = Profiler()
    p.start()
    try:
        assert profiling_threading._installed_thread_hooks == {}
        set_native_id = ddtrace_threading.Thread._set_native_id  # type: ignore[attr-defined]
        assert set_native_id.__module__ != profiling_threading.__name__
        assert not is_wrapped(asyncio.tasks._GatheringFuture.__init__)  # type: ignore[attr-defined]

        try:
            import gevent  # noqa: F401
        except ImportError:
            pass
        else:
            from ddtrace.profiling import _gevent

            assert not _gevent._greenlet_tracer_enabled
    finally:
        p.stop(flush=False)

    print("OK")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="0",
        DD_PROFILING_INSTALL="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_install_enabled_application_behavior_unchanged",
    ),
    out="OK\n",
    err=None,
)
def test_install_enabled_application_behavior_unchanged() -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import glob
    import os
    import threading

    results: list[int] = []
    results_lock = threading.Lock()

    def thread_work(i: int) -> None:
        with results_lock:
            results.append(i * i)

    threads = [threading.Thread(target=thread_work, args=(i,), name=f"worker-{i}") for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [i * i for i in range(8)], results
    assert all(t.native_id is not None for t in threads)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda x: x + 1, range(10))) == list(range(1, 11))

    async def child(i: int) -> int:
        await asyncio.sleep(0.01)
        return i * 2

    async def main() -> list[int]:
        task = asyncio.create_task(child(100), name="named-task")
        gathered = await asyncio.gather(*(child(i) for i in range(5)))
        done, _ = await asyncio.wait([asyncio.ensure_future(child(7))])
        loop = asyncio.get_running_loop()
        from_executor = await loop.run_in_executor(None, lambda: 42)
        return [*gathered, await task, done.pop().result(), from_executor]

    assert asyncio.run(main()) == [0, 2, 4, 6, 8, 200, 14, 42]

    asyncio_in_thread: list[int] = []
    t = threading.Thread(target=lambda: asyncio_in_thread.extend(asyncio.run(main())))
    t.start()
    t.join()
    assert asyncio_in_thread == [0, 2, 4, 6, 8, 200, 14, 42], asyncio_in_thread

    pid = os.fork()
    if pid == 0:
        child_thread = threading.Thread(target=lambda: None)
        child_thread.start()
        child_thread.join()
        os._exit(0 if asyncio.run(main()) == [0, 2, 4, 6, 8, 200, 14, 42] else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status

    # The hooks are installed, but the profiler must not run.
    assert not glob.glob(os.environ["DD_PROFILING_OUTPUT_PPROF"] + ".*")

    print("OK")


@pytest.mark.skipif(
    importlib.util.find_spec("gevent") is not None,
    reason="when gevent is installed, ddtrace-run reloads threading for the application, which the hooks do not patch",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="0",
        DD_PROFILING_INSTALL="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_install_enabled_runtime_start_samples_pre_started_thread",
    ),
    err=None,
)
def test_install_enabled_runtime_start_samples_pre_started_thread() -> None:
    import os
    import threading
    import time

    should_stop = threading.Event()

    def pre_started_thread_task() -> None:
        while not should_stop.is_set():
            start = time.monotonic()
            while time.monotonic() - start < 0.01:
                pass
            time.sleep(0.001)

    pre_started_thread = threading.Thread(target=pre_started_thread_task, name="pre-started-thread")
    pre_started_thread.start()

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils

    p = Profiler()
    p.start()
    try:
        time.sleep(1.0)
    finally:
        should_stop.set()
        pre_started_thread.join()
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="pre-started-thread",
            locations=[
                pprof_utils.StackLocation(
                    function_name=pre_started_thread_task.__name__,
                    filename="test_install.py",
                    line_no=-1,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="0",
        DD_PROFILING_INSTALL="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_install_enabled_runtime_start_samples_pre_started_asyncio_task",
    ),
    err=None,
)
def test_install_enabled_runtime_start_samples_pre_started_asyncio_task() -> None:
    import asyncio
    import os
    import time

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils

    async def pre_started_asyncio_task() -> None:
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            start = time.monotonic()
            while time.monotonic() - start < 0.01:
                pass
            await asyncio.sleep(0)

    p = Profiler()

    async def main() -> None:
        # The event loop and the task exist before the profiler starts.
        task = asyncio.create_task(pre_started_asyncio_task(), name="pre-started-task")
        await asyncio.sleep(0.05)
        p.start()
        await task

    try:
        asyncio.run(main())
    finally:
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="pre-started-task",
            locations=[
                pprof_utils.StackLocation(
                    function_name=pre_started_asyncio_task.__name__,
                    filename="test_install.py",
                    line_no=-1,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="0",
        DD_PROFILING_INSTALL="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_install_enabled_runtime_start_samples_asyncio_loop_in_other_thread",
    ),
    err=None,
)
def test_install_enabled_runtime_start_samples_asyncio_loop_in_other_thread() -> None:
    import asyncio
    import os
    import threading
    import time

    loop_started = threading.Event()
    should_stop = threading.Event()

    async def pre_started_asyncio_task() -> None:
        loop_started.set()
        while not should_stop.is_set():
            start = time.monotonic()
            while time.monotonic() - start < 0.01:
                pass
            await asyncio.sleep(0)

    async def main() -> None:
        await asyncio.create_task(pre_started_asyncio_task(), name="pre-started-task")

    loop_thread = threading.Thread(target=asyncio.run, args=(main(),), name="loop-thread")
    loop_thread.start()
    assert loop_started.wait(timeout=5)

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils

    p = Profiler()
    p.start()
    try:
        time.sleep(1.0)
    finally:
        should_stop.set()
        loop_thread.join()
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="loop-thread",
            task_name="pre-started-task",
            locations=[
                pprof_utils.StackLocation(
                    function_name=pre_started_asyncio_task.__name__,
                    filename="test_install.py",
                    line_no=-1,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="0",
        DD_PROFILING_INSTALL="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_install_enabled_runtime_start_samples_pre_started_greenlet",
    ),
    err=None,
)
def test_install_enabled_runtime_start_samples_pre_started_greenlet() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os
    import threading
    import time

    import gevent

    should_stop = threading.Event()

    def pre_started_greenlet_task() -> None:
        while not should_stop.is_set():
            start = time.time()
            while time.time() - start < 0.01:
                pass
            gevent.sleep(0)

    pre_started_greenlet = gevent.spawn(pre_started_greenlet_task)
    pre_started_greenlet.name = "pre-started-greenlet"
    gevent.sleep(0.05)

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import _main_thread_has_native_id

    p = Profiler()
    p.start()
    try:
        gevent.sleep(1.0)
    finally:
        should_stop.set()
        pre_started_greenlet.join(timeout=2)
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread" if _main_thread_has_native_id() else None,
            task_name="pre-started-greenlet",
            locations=[
                pprof_utils.StackLocation(
                    function_name=pre_started_greenlet_task.__name__,
                    filename="test_install.py",
                    line_no=-1,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="0", DD_PROFILING_INSTALL="1"),
    out="OK\n",
    err=None,
)
def test_install_enabled_profiler_restart_does_not_stack_hooks() -> None:
    import threading

    from ddtrace.internal._unpatched import _threading as ddtrace_threading
    from ddtrace.profiling import Profiler
    from ddtrace.profiling.collector import threading as profiling_threading

    hooks = dict(profiling_threading._installed_thread_hooks)
    assert set(hooks) == {"_set_native_id", "_bootstrap_inner"}, hooks

    for _ in range(3):
        p = Profiler()
        p.start()
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        assert t.native_id is not None
        p.stop(flush=False)

        assert profiling_threading._installed_thread_hooks == hooks
        assert ddtrace_threading.Thread._set_native_id is hooks["_set_native_id"]  # type: ignore[attr-defined]
        assert ddtrace_threading.Thread._bootstrap_inner is hooks["_bootstrap_inner"]  # type: ignore[attr-defined]

    print("OK")
