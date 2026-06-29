"""Introspection invariants frameworks rely on after wrapping.

``inspect.signature``, ``__name__``/``__doc__``, and the
``is{generator,coroutine,asyncgen}function`` predicates must all survive
wrapping, otherwise FastAPI/pydantic/Typer-style signature inspection and
dd-trace's own kind detection break.
"""

import inspect


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
