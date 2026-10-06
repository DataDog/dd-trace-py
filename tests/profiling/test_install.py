"""
Contracts for when the stack profiler hooks get installed, depending on DD_PROFILING_INSTALL.

The various cases are combinations of DD_PROFILING_ENABLED, DD_PROFILING_INSTALL and
DD_PROFILING_STACK values.
"""

import sys

import pytest

from ddtrace.internal.datadog.profiling import stack as _stack_ext


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
