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
