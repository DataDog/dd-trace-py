# -*- coding: utf-8 -*-

import inspect

import pytest

from ddtrace.debugging import _safety


GLOBAL_VALUE = 42


def test_get_args():
    def assert_args(args):
        assert set(dict(_safety.get_args(inspect.currentframe().f_back)).keys()) == args

    def assert_locals(_locals):
        assert set(dict(_safety.get_locals(inspect.currentframe().f_back)).keys()) == _locals | {
            "assert_args",
            "assert_locals",
        }

    def assert_globals(_globals):
        assert set(dict(_safety.get_globals(inspect.currentframe().f_back)).keys()) == _globals

    def arg_and_kwargs(a, **kwargs):
        assert_args({"a", "kwargs"})
        assert_locals(set())

    def arg_and_args_and_kwargs(a, *ars, **kwars):
        assert_args({"a", "ars", "kwars"})
        assert_locals(set())

    def args_and_kwargs(*ars, **kwars):
        assert_args({"ars", "kwars"})
        assert_locals(set())

    def args(*ars):
        assert_args({"ars"})
        assert_locals(set())

    # `co_varnames` lays out keyword-only arguments BEFORE
    # `*args` / `**kwargs`. The previous nargs offset
    # `co_argcount + VARARGS + VARKEYWORDS` omitted `co_kwonlyargcount`,
    # so for `def f(a, *args, c, **kwargs)` keyword-only `c` was
    # mis-classified as positional and the actual `args` / `kwargs`
    # objects fell out of `get_args` entirely (and got reported by
    # `get_locals` instead).
    def kwonly_and_args_and_kwargs(a, *ars, c, **kwars):
        local = 1  # noqa
        assert_args({"a", "ars", "c", "kwars"})
        assert_locals({"local"})

    def kwonly_only(a, *, c, d):
        local = 1  # noqa
        assert_args({"a", "c", "d"})
        assert_locals({"local"})

    def referenced_globals():
        global GLOBAL_VALUE
        a = GLOBAL_VALUE >> 1  # noqa

        assert_globals({"GLOBAL_VALUE"})

    arg_and_kwargs(1, b=2)
    arg_and_args_and_kwargs(1, 42, b=2)
    args_and_kwargs()
    args()
    kwonly_and_args_and_kwargs(1, 42, c=3, extra=9)
    kwonly_only(1, c=3, d=4)
    referenced_globals()


# ---- Side effects ----


class SideEffects(object):
    class SideEffect(Exception):
        pass

    def __getattribute__(self, name):
        raise SideEffects.SideEffect()

    def __get__(self, instance, owner):
        raise self.SideEffect()

    @property
    def property_with_side_effect(self):
        raise self.SideEffect()


def test_get_fields_side_effects():
    assert _safety.get_fields(SideEffects()) == {}


# ---- Slots ----


def test_get_fields_slots():
    class A(object):
        __slots__ = ["a"]

        def __init__(self):
            self.a = "a"

    class B(A):
        __slots__ = ["b"]

        def __init__(self):
            super(B, self).__init__()
            self.b = "b"

    assert _safety.get_fields(A()) == {"a": "a"}
    assert _safety.get_fields(B()) == {"a": "a", "b": "b"}


def test_safe_dict():
    # Found in the FastAPI test suite
    class Foo(object):
        @property
        def __dict__(self):
            raise NotImplementedError()

    with pytest.raises(AttributeError):
        _safety._safe_dict(Foo())
