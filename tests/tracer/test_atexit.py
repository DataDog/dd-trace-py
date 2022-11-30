import pytest

from ddtrace.internal import atexit


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
