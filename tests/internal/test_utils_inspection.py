from functools import wraps
from pathlib import Path

from ddtrace.internal.utils.inspection import undecorated


def test_undecorated():
    def d(f):
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    def f():
        pass

    df = d(f)
    assert df is not f

    ddf = d(df)
    assert ddf is not df

    dddf = d(ddf)
    assert dddf is not ddf

    name, path = f.__code__.co_name, Path(__file__).resolve()
    assert f is undecorated(dddf, name, path)
    assert f is undecorated(ddf, name, path)
    assert f is undecorated(df, name, path)
    assert f is undecorated(f, name, path)

    assert undecorated(undecorated, name, path) is undecorated


def test_class_decoration():
    class Decorator:
        def __init__(self, f):
            self.f = f

    @Decorator
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


def test_wrapped_decoration():
    @wraps
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


def test_undecorated_plain_function_returns_input_directly():
    # A plain (undecorated) function is the common case. ``undecorated`` must return it
    # without descending into the BFS / ``__dir__()`` scan: the input itself already
    # matches. This is the fast path that keeps per-test source-location discovery cheap.
    def f():
        pass

    name, path = f.__code__.co_name, Path(__file__).resolve()
    assert undecorated(f, name, path) is f

    # A function whose name does not match must not short-circuit on the input.
    def g():
        pass

    assert undecorated(g, name="does_not_exist", path=path) is g
