"""Introspection invariants frameworks rely on after wrapping.

``inspect.signature``, ``__name__``/``__qualname__``/``__module__``/``__doc__``,
user-set function attributes, the ``is{generator,coroutine,asyncgen}function``
predicates, and "is this still a function?" checks (``callable`` /
``inspect.isfunction`` / ``isinstance(..., FunctionType)``) must all survive
wrapping, otherwise FastAPI/pydantic/Typer-style inspection, decorator-stashed
metadata, and dd-trace's own kind detection break.

Invariants that hold for every mechanism are asserted plainly. Properties that a
particular mechanism does NOT preserve are codified too: the test asserts the
transparent/ideal behaviour and the diverging mechanism carries a strict
``@xfail_mechanism`` -- so the expected difference is documented in code, and if
that mechanism ever starts preserving the property the XPASS fails CI and forces
the marker to be removed. (We assert the real-function behaviour, e.g.
``type(g) is function``; we never assert a proxy type as if it were correct.)
"""

import inspect
import types

from tests.wrapping.mechanisms import xfail_mechanism


def test_signature_preserved(mech):
    def f(a, b, c=3, *args, k, **kwargs):
        return (a, b, c, args, k, kwargs)

    g = mech.wrap_function(f)
    assert str(inspect.signature(g)) == "(a, b, c=3, *args, k, **kwargs)"


def test_positional_only_signature_preserved(mech):
    def f(a, b, /, c=3):
        return (a, b, c)

    g = mech.wrap_function(f)
    assert str(inspect.signature(g)) == "(a, b, /, c=3)"


def test_name_and_doc_preserved(mech):
    def f(a):
        """the docstring"""
        return a

    g = mech.wrap_function(f)
    assert g.__name__ == "f"
    assert g.__doc__ == "the docstring"


def test_qualname_and_module_preserved(mech):
    def f(a):
        return a

    expected = (f.__qualname__, f.__module__)
    g = mech.wrap_function(f)
    assert (g.__qualname__, g.__module__) == expected


def test_user_attributes_preserved(mech):
    # Decorators commonly stash metadata as attributes on the function object
    # (e.g. a router's route table); wrapping must not lose them.
    def f(a):
        return a

    f.route = "/widgets"
    g = mech.wrap_function(f)
    assert g.route == "/widgets"


def test_wrapped_is_still_function_like(mech):
    # FastAPI/pydantic/Typer and dd-trace itself gate behaviour on these checks.
    # wrapt returns a proxy whose ``type()`` differs, but it forwards ``__class__``
    # so isinstance/isfunction still hold -- assert the checks that are uniform.
    def f(a):
        return a

    g = mech.wrap_function(f)
    assert callable(g)
    assert inspect.isfunction(g)
    assert isinstance(g, types.FunctionType)


@xfail_mechanism(
    "wrapt",
    reason="wrapt returns a transparent FunctionWrapper proxy, so type(g) is the proxy class, "
    "not types.FunctionType (isinstance still passes -- see test_wrapped_is_still_function_like)",
)
def test_type_is_exactly_function(mech):
    # The bytecode mechanisms mutate the function in place, so it stays a real
    # function; code doing a strict ``type(x) is FunctionType`` check still works.
    def f(a):
        return a

    g = mech.wrap_function(f)
    assert type(g) is types.FunctionType


@xfail_mechanism(
    "tracer_wrap",
    reason="@tracer.wrap() runs the body inside a functools.wraps wrapper taking *args/**kwargs, "
    "so __defaults__/__kwdefaults__ live on the wrapper and read as None (inspect.signature still "
    "works via __wrapped__, but direct __defaults__ access is lossy)",
)
def test_defaults_preserved(mech):
    def f(a, b=2, *, k=3):
        return (a, b, k)

    g = mech.wrap_function(f)
    assert g.__defaults__ == (2,)
    assert g.__kwdefaults__ == {"k": 3}


@xfail_mechanism(
    "tracer_wrap",
    reason="@tracer.wrap() executes the body inside a wrapper function, so __code__.co_name is the "
    "wrapper's name (func_wrapper), not the wrapped function's -- visible in tracebacks/profilers",
)
def test_code_name_preserved(mech):
    def f(a):
        return a

    g = mech.wrap_function(f)
    assert g.__code__.co_name == "f"


def test_annotations_preserved(mech):
    def f(a: int, b: str = "x") -> tuple:
        return (a, b)

    g = mech.wrap_function(f)
    assert inspect.signature(g).return_annotation is tuple
    params = inspect.signature(g).parameters
    assert params["a"].annotation is int
    assert params["b"].annotation is str


def test_isgeneratorfunction_detected(mech):
    def g():
        yield 1

    assert inspect.isgeneratorfunction(mech.wrap_function(g))


def test_iscoroutinefunction_detected(mech):
    async def c():
        return 1

    assert inspect.iscoroutinefunction(mech.wrap_function(c))


def test_isasyncgenfunction_detected(mech):
    async def ag():
        yield 1

    assert inspect.isasyncgenfunction(mech.wrap_function(ag))
