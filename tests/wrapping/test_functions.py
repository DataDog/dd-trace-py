"""Plain synchronous functions: signatures, closures, decorators, exceptions.

Every test wraps a hand-written function with each of the four mechanisms and
asserts the explicit expected behaviour. The wrapped callable must be
indistinguishable from the original.
"""

import functools

import pytest

from tests.wrapping._harness import mechanisms
from tests.wrapping._harness import wraps_deco


# --- signatures ------------------------------------------------------------
@mechanisms
def test_no_args(mech):
    def f():
        return 42

    g = mech.wrap_function(f)
    assert g() == 42


@mechanisms
def test_positional(mech):
    def f(a, b):
        return (a, b)

    g = mech.wrap_function(f)
    assert g(1, 2) == (1, 2)
    assert g(1, b=2) == (1, 2)


@mechanisms
def test_defaults(mech):
    def f(a, b=2, c=3):
        return (a, b, c)

    g = mech.wrap_function(f)
    assert g(1) == (1, 2, 3)
    assert g(1, 20) == (1, 20, 3)
    assert g(1, c=30) == (1, 2, 30)


@mechanisms
def test_positional_only(mech):
    def f(a, b, /, c=3):
        return (a, b, c)

    g = mech.wrap_function(f)
    assert g(1, 2) == (1, 2, 3)
    assert g(1, 2, c=9) == (1, 2, 9)
    assert g(1, 2, 9) == (1, 2, 9)


@mechanisms
def test_keyword_only(mech):
    def f(a, *, b, c=3):
        return (a, b, c)

    g = mech.wrap_function(f)
    assert g(1, b=2) == (1, 2, 3)
    assert g(1, b=2, c=9) == (1, 2, 9)


@mechanisms
def test_var_args(mech):
    def f(*args):
        return args

    g = mech.wrap_function(f)
    assert g() == ()
    assert g(1, 2, 3) == (1, 2, 3)


@mechanisms
def test_var_kwargs(mech):
    def f(**kwargs):
        return kwargs

    g = mech.wrap_function(f)
    assert g() == {}
    assert g(x=1, y=2) == {"x": 1, "y": 2}


@mechanisms
def test_full_signature(mech):
    def f(a, b=2, /, c=3, *args, k, m=5, **kw):
        return (a, b, c, args, k, m, kw)

    g = mech.wrap_function(f)
    assert g(1, k=9) == (1, 2, 3, (), 9, 5, {})
    assert g(1, 2, 3, 4, 5, k=9, z=10) == (1, 2, 3, (4, 5), 9, 5, {"z": 10})


@mechanisms
def test_positional_or_keyword_passed_by_keyword(mech):
    def f(a, b, c=None):
        return (a, b, c)

    g = mech.wrap_function(f)
    assert g(1, b=2, c=3) == (1, 2, 3)


# --- closures / nesting / lambdas ------------------------------------------
@mechanisms
def test_closure_free_var(mech):
    answer = 42

    def f(a):
        return (a, answer)

    g = mech.wrap_function(f)
    assert g(1) == (1, 42)


@mechanisms
def test_kwargs_captured_by_closure(mech):
    # Regression: **kwargs captured by an inner closure becomes a cell var and
    # must be loaded with LOAD_DEREF (not LOAD_FAST) on 3.11+.
    def f(a, **kwargs):
        def inner():
            return kwargs

        return (a, inner())

    g = mech.wrap_function(f)
    assert g(1, x=2, y=3) == (1, {"x": 2, "y": 3})


@mechanisms
def test_kwonly_captured_by_closure(mech):
    def f(a, *, key=None, **kwargs):
        def inner():
            return (key, kwargs)

        return (a, inner())

    g = mech.wrap_function(f)
    assert g(1, key="k", x=2) == (1, ("k", {"x": 2}))


@mechanisms
def test_nested_factory(mech):
    def outer(answer=42):
        def inner(a, b):
            return (a, b, answer)

        return inner

    g = mech.wrap_function(outer)
    assert g()(1, 2) == (1, 2, 42)


@mechanisms
def test_lambda(mech):
    f = lambda a, b=2: (a, b)  # noqa: E731
    g = mech.wrap_function(f)
    assert g(1) == (1, 2)
    assert g(1, 3) == (1, 3)


@mechanisms
def test_recursion(mech):
    def fact(n):
        return 1 if n <= 1 else n * fact(n - 1)

    # Rebind the name the body recurses through, so every recursive call -- not
    # just the outermost -- goes through the wrapper. For the new-object mechanisms
    # (tracer_wrap, wrapt) `g = wrap(fact)` would leave `fact(n - 1)` calling the
    # original; rebinding `fact` makes the closure cell point at the wrapped object.
    fact = mech.wrap_function(fact)
    assert fact(6) == 720


# --- exceptions ------------------------------------------------------------
@mechanisms
def test_exception_propagates(mech):
    def f(x):
        raise ValueError(f"boom-{x}")

    g = mech.wrap_function(f)
    with pytest.raises(ValueError, match="boom-7"):
        g(7)


@mechanisms
def test_exception_traceback_points_at_user_frame(mech):
    def f():
        raise ValueError("here")

    raise_line = f.__code__.co_firstlineno + 1  # the `raise` is the line after `def f():`

    g = mech.wrap_function(f)
    # pytest.raises makes "the exception was swallowed" a hard failure rather than
    # a vacuous pass.
    with pytest.raises(ValueError) as exc_info:
        g()

    # The deepest frame must be the user's function AT the raising line: wrapping
    # must not shift where the exception is attributed (asserting the name alone is
    # too weak -- the deepest frame is always the raiser regardless of fidelity).
    deepest = exc_info.tb
    while deepest.tb_next is not None:
        deepest = deepest.tb_next
    assert deepest.tb_frame.f_code.co_name == "f"
    assert deepest.tb_lineno == raise_line


# --- decorator order -------------------------------------------------------
@mechanisms
def test_decorator_wraps_outer(mech):
    # mechanism applied to the raw function, ordinary decorator on the outside
    def f(a, b):
        return (a, b)

    g = wraps_deco(mech.wrap_function(f))
    assert g(1, 2) == (1, 2)


@mechanisms
def test_decorator_wraps_inner(mech):
    # ordinary decorator on the function, mechanism on the outside
    def f(a, b):
        return (a, b)

    g = mech.wrap_function(wraps_deco(f))
    assert g(1, 2) == (1, 2)


@mechanisms
def test_double_decorator(mech):
    def f(a, b):
        return (a, b)

    g = wraps_deco(wraps_deco(mech.wrap_function(f)))
    assert g(1, 2) == (1, 2)


@mechanisms
def test_lru_cache_over_wrapped(mech):
    calls = {"n": 0}

    def expensive(x):
        calls["n"] += 1
        return x * x

    cached = functools.lru_cache(maxsize=None)(mech.wrap_function(expensive))
    assert cached(7) == 49
    assert cached(7) == 49
    assert calls["n"] == 1
