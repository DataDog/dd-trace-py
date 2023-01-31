# -*- coding: utf-8 -*-

import inspect

from ddtrace.debugging import safety


def test_get_args():
    def assert_args(args):
        assert set(dict(safety.get_args(inspect.currentframe().f_back)).keys()) == args

    def assert_locals(_locals):
        assert set(dict(safety.get_locals(inspect.currentframe().f_back)).keys()) == _locals

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

    arg_and_kwargs(1, b=2)
    arg_and_args_and_kwargs(1, 42, b=2)
    args_and_kwargs()
    args()


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
    assert safety.get_fields(SideEffects()) == {}


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

    assert safety.get_fields(A()) == {"a": "a"}
    assert safety.get_fields(B()) == {"a": "a", "b": "b"}
