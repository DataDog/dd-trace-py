"""Unwrap / restore -- the removal half of the wrap contract.

Only the two mechanisms that mutate a function *in place* have a symmetric
dd-trace unwrap to exercise: ``ddtrace.internal.wrapping.{wrap,unwrap}`` and
``WrappingContext.{wrap,unwrap}``. ``tracer.wrap()`` returns a *new* wrapper (the
original is never mutated, so there is nothing to restore) and the wrapt path
patches an attribute by name (undone by reassigning it), so neither has an
in-place restore to cover here.

These tests assert a wrap->unwrap round-trip restores call behaviour and the
signature, and pin a divergence in how completely the original is reinstated:
``internal_wrap`` puts back the exact original ``__code__`` object (a true
inverse, even when layers are nested); ``WrappingContext.unwrap`` restores
behaviour but rebuilds the code object rather than reinstating the original, so
``__code__`` identity is not restored (codified with a strict xfail).

Mechanism-specific (the matrix's other two mechanisms have no in-place unwrap), so
these opt out of the all-mechanisms ``mech`` guardrail.
"""

import inspect

import pytest

from ddtrace.internal.wrapping import unwrap as _internal_unwrap
from ddtrace.internal.wrapping import wrap as _internal_wrap
from ddtrace.internal.wrapping.context import WrappingContext


pytestmark = pytest.mark.mechanism_specific


def _noop(wrapped, args, kwargs):
    return wrapped(*args, **kwargs)


def _noop2(wrapped, args, kwargs):
    return wrapped(*args, **kwargs)


class _NoopContext(WrappingContext):
    pass


class _InternalRestore:
    """internal wrap()/unwrap() round-trip on a function (mutates in place)."""

    def wrap(self, fn):
        _internal_wrap(fn, _noop)

    def unwrap(self, fn):
        _internal_unwrap(fn, _noop)


class _ContextRestore:
    """WrappingContext wrap()/unwrap(); the instance is retained to unwrap with."""

    def __init__(self):
        self._ctx = {}

    def wrap(self, fn):
        ctx = _NoopContext(fn)
        ctx.wrap()
        self._ctx[fn] = ctx

    def unwrap(self, fn):
        self._ctx.pop(fn).unwrap()


@pytest.fixture(params=[_InternalRestore, _ContextRestore], ids=["internal_wrap", "wrapping_context"])
def restore(request):
    return request.param()


def test_roundtrip_restores_behavior_and_signature(restore):
    def f(a, b=2, *, k=3):
        return (a, b, k)

    sig = str(inspect.signature(f))
    assert f(1) == (1, 2, 3)
    restore.wrap(f)
    assert f(1) == (1, 2, 3)  # transparent while wrapped
    restore.unwrap(f)
    assert f(1) == (1, 2, 3)  # behaviour restored after unwrap
    assert str(inspect.signature(f)) == sig


def test_internal_wrap_unwrap_reinstates_original_code():
    # internal wrap() is a true inverse: unwrap restores the exact original code
    # object, leaving no trampoline behind.
    def f(a):
        return a

    orig = f.__code__
    _internal_wrap(f, _noop)
    _internal_unwrap(f, _noop)
    assert f.__code__ is orig


def test_internal_wrap_nested_unwrap_restores():
    # Two layers, unwrapped LIFO, must restore the original (no dropped/leftover layer).
    def f(a):
        return a

    orig = f.__code__
    _internal_wrap(f, _noop)
    _internal_wrap(f, _noop2)
    assert f(7) == 7
    _internal_unwrap(f, _noop2)
    _internal_unwrap(f, _noop)
    assert f(7) == 7
    assert f.__code__ is orig


@pytest.mark.xfail(
    strict=True,
    reason="WrappingContext.unwrap restores behaviour but rebuilds the code object instead of "
    "reinstating the original, so __code__ identity is not restored after a wrap->unwrap round-trip",
)
def test_wrapping_context_unwrap_reinstates_original_code():
    def f(a):
        return a

    orig = f.__code__
    ctx = _NoopContext(f)
    ctx.wrap()
    ctx.unwrap()
    assert f.__code__ is orig
