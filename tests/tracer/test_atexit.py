import mock
import pytest

from ddtrace.internal import atexit
from ddtrace.internal.settings._config import config


def test_register():
    def foobar():
        pass

    atexit.register(foobar)
    atexit.unregister(foobar)
    atexit.unregister(foobar)


@pytest.mark.subprocess(out="hello\nworld\n")
def test_prog_register():
    from ddtrace.internal import atexit

    def foobar(what):
        print("hello")
        print(what)

    atexit.register(foobar, "world")


@pytest.mark.subprocess
def test_prog_unregister():
    from ddtrace.internal import atexit

    def foobar():
        print("hello")

    atexit.register(foobar)
    atexit.unregister(foobar)


def test_tracer_atexit_hooks_not_registered_when_disabled():
    from ddtrace._trace.tracer import Tracer

    with mock.patch.object(config, "_tracer_atexit_hooks_enabled", False):
        with mock.patch.object(atexit, "register") as mock_register:
            with mock.patch.object(atexit, "register_on_exit_signal") as mock_signal:
                t = Tracer()
                registered = [c.args[0] for c in mock_register.call_args_list]
                assert t._atexit not in registered
                signal_registered = [c.args[0] for c in mock_signal.call_args_list]
                assert t._atexit not in signal_registered
                t.shutdown()
