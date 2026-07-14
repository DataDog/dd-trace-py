"""Synchronous generators: iteration, send/throw/close, return value, yield from.

Drives each lifecycle operation explicitly and asserts the exact sequence the
wrapped generator must produce.
"""

import sys

import pytest

from tests.wrapping.mechanisms import xfail_mechanism


def test_iterate(mech):
    def g():
        yield 1
        yield 2
        yield 3

    assert list(mech.wrap_function(g)()) == [1, 2, 3]


def test_send(mech):
    def g():
        x = yield 0
        while True:
            x = yield x

    gen = mech.wrap_function(g)()
    assert next(gen) == 0
    assert gen.send(10) == 10
    assert gen.send(20) == 20
    gen.close()


def test_throw_recovered(mech):
    def g():
        while True:
            try:
                yield "value"
            except ValueError:
                yield "recovered"

    gen = mech.wrap_function(g)()
    assert next(gen) == "value"
    assert gen.throw(ValueError()) == "recovered"
    gen.close()


def test_throw_propagates(mech):
    def g():
        yield 1
        yield 2

    gen = mech.wrap_function(g)()
    assert next(gen) == 1
    with pytest.raises(KeyError):
        gen.throw(KeyError("boom"))


def test_close_runs_finally(mech):
    log = []

    def g():
        try:
            yield 1
            yield 2
        finally:
            log.append("cleanup")

    gen = mech.wrap_function(g)()
    assert next(gen) == 1
    gen.close()
    assert log == ["cleanup"]


@xfail_mechanism(
    "wrapping_context",
    reason="WrappingContext.throw() on an unstarted generator crashes on 3.11+ (internal AttributeError)",
    condition=sys.version_info >= (3, 11),
)
def test_throw_on_unstarted_generator(mech):
    def g():
        yield 1
        yield 2

    gen = mech.wrap_function(g)()
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))


def test_yield_from(mech):
    def g():
        yield from range(5)

    assert list(mech.wrap_function(g)()) == [0, 1, 2, 3, 4]


@xfail_mechanism(
    "internal_wrap",
    reason="G22: internal wrap() drops generator return value (generators.py @stopiter returns None)",
)
def test_return_value_via_stopiteration(mech):
    def g():
        yield 1
        return "RETVAL"

    gen = mech.wrap_function(g)()
    assert next(gen) == 1
    with pytest.raises(StopIteration) as exc_info:
        next(gen)
    assert exc_info.value.value == "RETVAL"


@xfail_mechanism(
    "internal_wrap",
    reason="G22: internal wrap() drops generator return value (generators.py @stopiter returns None)",
)
def test_yield_from_return_value_wrapped_delegatee(mech):
    # The wrapped generator is the delegatee (sub); an unwrapped parent receives
    # its return value via `yield from`.
    def sub():
        yield 1
        return "SUB"

    wrapped_sub = mech.wrap_function(sub)

    def parent():
        result = yield from wrapped_sub()
        yield ("got", result)

    assert list(parent()) == [1, ("got", "SUB")]


@xfail_mechanism(
    "internal_wrap",
    reason="G22: internal wrap() drops generator return value (generators.py @stopiter returns None)",
)
def test_yield_from_return_value_wrapped_delegator(mech):
    # The wrapped generator is the delegator (parent): its OWN return value is the
    # one produced by `yield from`. Covers the wrapped-delegator case that wrapping
    # only the delegatee would miss for the new-object mechanisms.
    def sub():
        yield 1
        return "SUB"

    def parent():
        return (yield from sub())

    gen = mech.wrap_function(parent)()
    assert next(gen) == 1
    with pytest.raises(StopIteration) as exc_info:
        next(gen)
    assert exc_info.value.value == "SUB"


def test_yield_from_throw_delegates_into_subgenerator(mech):
    def sub():
        try:
            yield 1
            yield 2
        except KeyError:
            yield "sub-caught"

    wrapped_sub = mech.wrap_function(sub)

    def parent():
        yield from wrapped_sub()

    gen = parent()
    assert next(gen) == 1
    assert gen.throw(KeyError()) == "sub-caught"
