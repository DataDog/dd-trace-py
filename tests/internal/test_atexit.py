import pytest

from ddtrace.internal import atexit


def test_register() -> None:
    def foobar() -> None:
        pass

    atexit.register(foobar)
    atexit.unregister(foobar)
    atexit.unregister(foobar)


@pytest.mark.subprocess(out="hello\nworld\n")
def test_prog_register() -> None:
    from ddtrace.internal import atexit

    def foobar(what: str) -> None:
        print("hello")
        print(what)

    atexit.register(foobar, "world")


@pytest.mark.subprocess
def test_prog_unregister() -> None:
    from ddtrace.internal import atexit

    def foobar() -> None:
        print("hello")

    atexit.register(foobar)
    atexit.unregister(foobar)


@pytest.mark.subprocess(env={"DD_TESTING_RAISE": None})
def test_atexit_exception_swallowed_in_production() -> None:
    """Exceptions raised by atexit handlers are swallowed when DD_TESTING_RAISE is not set."""
    from ddtrace.internal import atexit

    def bad_handler() -> None:
        raise RuntimeError("boom")

    atexit.register(bad_handler)


@pytest.mark.subprocess(err=lambda s: "RuntimeError: boom" in s, env={"DD_TESTING_RAISE": "1"})
def test_atexit_exception_reraised_when_testing_raise() -> None:
    """Exceptions raised by atexit handlers are logged to stderr when DD_TESTING_RAISE=1.

    Python's atexit machinery always catches re-raised exceptions and prints them;
    the process still exits with code 0.
    """
    from ddtrace.internal import atexit

    def bad_handler() -> None:
        raise RuntimeError("boom")

    atexit.register(bad_handler)


@pytest.mark.subprocess
def test_unregister_removes_wrapped_handler() -> None:
    """Unregistering a handler prevents it from running at exit."""
    from ddtrace.internal import atexit

    def handler() -> None:
        print("should not run")

    atexit.register(handler)
    atexit.unregister(handler)


def test_unregister_uses_wrapper_mapping() -> None:
    """unregister() looks up the safe-wrapper in _atexit_wrapped and removes it."""
    from ddtrace.internal import atexit as dd_atexit

    def handler() -> None:
        pass

    dd_atexit.register(handler)
    assert handler in dd_atexit._atexit_wrapped

    dd_atexit.unregister(handler)
    assert handler not in dd_atexit._atexit_wrapped


@pytest.mark.subprocess(status=-15, env={"DD_TESTING_RAISE": None})
def test_register_on_exit_signal_exception_swallowed() -> None:
    """Exceptions raised in a signal handler registered via register_on_exit_signal are swallowed.

    signals.handle_signal installs a _raise_default base handler that re-raises SIGTERM
    with SIG_DFL after all registered handlers run, so the process always exits via the
    signal (status -15).  The key assertion is that no traceback appears in stderr.
    """
    import os
    import signal

    from ddtrace.internal import atexit

    def bad_handler() -> None:
        raise RuntimeError("boom in signal handler")

    atexit.register_on_exit_signal(bad_handler)
    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.subprocess(out="still alive\n")
def test_register_on_exit_signal_preserves_ignored_signal() -> None:
    """A signal that the application ignores neither terminates the process nor runs the handlers."""
    import os
    import signal

    from ddtrace.internal import atexit

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def handler() -> None:
        print("handler called")

    atexit.register_on_exit_signal(handler)
    for sig in (signal.SIGTERM, signal.SIGINT):
        assert signal.getsignal(sig) is signal.SIG_IGN
        os.kill(os.getpid(), sig)
    print("still alive")


@pytest.mark.subprocess(
    status=1, err=lambda s: "RuntimeError: boom in signal handler" in s, env={"DD_TESTING_RAISE": "1"}
)
def test_register_on_exit_signal_exception_reraised_when_testing_raise() -> None:
    """Exceptions in a signal handler are re-raised when DD_TESTING_RAISE=1."""
    import os
    import signal

    from ddtrace.internal import atexit

    def bad_handler() -> None:
        raise RuntimeError("boom in signal handler")

    atexit.register_on_exit_signal(bad_handler)
    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.subprocess
def test_exit_signal_callbacks_installs_once() -> None:
    """Repeated install() calls keep one handler in place."""
    import signal
    from unittest import mock

    from ddtrace.internal import atexit

    callbacks = atexit.ExitSignalCallbacks()
    with mock.patch.object(atexit, "register_on_exit_signal", wraps=atexit.register_on_exit_signal) as register:
        callbacks.install()
        handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        callbacks.install()
        callbacks.install()

    assert register.call_count == 1
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == handlers


@pytest.mark.subprocess(status=-15, out="callback\napp handler\n")
def test_exit_signal_callbacks_reinstalls_after_app_replaces_handler() -> None:
    """install() re-installs when the application replaced the handler, chaining the app's one."""
    import os
    import signal

    from ddtrace.internal import atexit

    callbacks = atexit.ExitSignalCallbacks()
    callbacks.add(lambda: print("callback", flush=True))
    callbacks.install()

    def app_handler(sig, frame):
        print("app handler", flush=True)
        signal.signal(sig, signal.SIG_DFL)
        signal.raise_signal(sig)

    signal.signal(signal.SIGTERM, app_handler)
    callbacks.install()
    assert signal.getsignal(signal.SIGTERM) is not app_handler
    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.subprocess
def test_exit_signal_callbacks_off_main_thread_keeps_state() -> None:
    """install() off the main thread is a no-op and keeps the saved handlers."""
    import signal
    import threading

    from ddtrace.internal import atexit

    callbacks = atexit.ExitSignalCallbacks()
    worker = threading.Thread(target=callbacks.install)
    worker.start()
    worker.join()
    assert callbacks._installed_handlers is None

    callbacks.install()
    saved = callbacks._installed_handlers
    handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    assert saved is not None

    worker = threading.Thread(target=callbacks.install)
    worker.start()
    worker.join()
    assert callbacks._installed_handlers == saved

    # A later main-thread call finds the handler in place and does not chain another.
    callbacks.install()
    assert callbacks._installed_handlers == saved
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == handlers


@pytest.mark.subprocess(status=-15, out="kept\n")
def test_exit_signal_callbacks_discard_stops_callback() -> None:
    """A discarded callback no longer runs on an exit signal."""
    import os
    import signal

    from ddtrace.internal import atexit

    def kept() -> None:
        print("kept", flush=True)

    def dropped() -> None:
        print("dropped", flush=True)

    callbacks = atexit.ExitSignalCallbacks()
    callbacks.add(kept)
    callbacks.add(dropped)
    callbacks.install()
    callbacks.discard(dropped)
    os.kill(os.getpid(), signal.SIGTERM)


@pytest.mark.subprocess(out="ok\n", env={"DD_TESTING_RAISE": None})
def test_exit_signal_callbacks_raising_callback_does_not_skip_others() -> None:
    """A raising callback is swallowed and the remaining callbacks still run."""
    from ddtrace.internal import atexit

    def bad() -> None:
        raise RuntimeError("boom")

    callbacks = atexit.ExitSignalCallbacks()
    callbacks.add(bad)
    callbacks.add(lambda: print("ok", flush=True))
    callbacks._run()


@pytest.mark.subprocess(env={"DD_TESTING_RAISE": "1"})
def test_exit_signal_callbacks_raising_callback_reraised_when_testing_raise() -> None:
    """A raising callback is re-raised when DD_TESTING_RAISE=1."""
    import pytest

    from ddtrace.internal import atexit

    def bad() -> None:
        raise RuntimeError("boom")

    callbacks = atexit.ExitSignalCallbacks()
    callbacks.add(bad)
    with pytest.raises(RuntimeError, match="boom"):
        callbacks._run()


@pytest.mark.subprocess(out="still alive\n")
def test_exit_signal_callbacks_install_keeps_ignored_signals() -> None:
    """Repeated install() calls leave ignored SIGTERM and SIGINT ignored, without registering again."""
    import os
    import signal
    from unittest import mock

    from ddtrace.internal import atexit

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    callbacks = atexit.ExitSignalCallbacks()
    callbacks.add(lambda: print("callback called", flush=True))
    with mock.patch.object(atexit, "register_on_exit_signal", wraps=atexit.register_on_exit_signal) as register:
        callbacks.install()
        callbacks.install()

    assert register.call_count == 1
    assert callbacks._installed_handlers == (signal.SIG_IGN, signal.SIG_IGN)
    for sig in (signal.SIGTERM, signal.SIGINT):
        assert signal.getsignal(sig) is signal.SIG_IGN
        os.kill(os.getpid(), sig)
    print("still alive")
