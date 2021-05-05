from ddtrace.internal import atexit


def test_register():
    def foobar():
        pass

    atexit.register(foobar)
    atexit.unregister(foobar)
    atexit.unregister(foobar)


def test_prog_register(run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.internal import atexit

def foobar(what):
    print("hello")
    print(what)

atexit.register(foobar, "world")
"""
    )
    assert status == 0
    assert out == b"hello\nworld\n"
    assert err == b""


def test_prog_unregister(run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.internal import atexit

def foobar():
    print("hello")

atexit.register(foobar)
atexit.unregister(foobar)
"""
    )
    assert status == 0
    assert out == b""
    assert err == b""
