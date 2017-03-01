# -*- coding: utf-8 -*-
from nose.tools import eq_

from ddtrace.contrib import func_name


class SomethingCallable(object):
    """
    A dummy class that implements __call__().
    """

    value = 42

    def __call__(self):
        return "something"

    def me(self):
        return self

    @staticmethod
    def add(a,b):
        return a + b

    @classmethod
    def answer(cls):
        return cls.value

def SomeFunc():
    """
    A function doing nothing.
    """
    return "nothing"


class TestContrib(object):
    """
    Testing contrib helper funcs.
    """

    def test_func_name(self):
        """
        Check that func_name works on anything callable, not only funcs.
        """
        eq_("nothing", SomeFunc())
        eq_("tests.contrib.test.SomeFunc", func_name(SomeFunc))

        f = SomethingCallable()
        eq_("something", f())
        eq_("tests.contrib.test.SomethingCallable", func_name(f))

        eq_(f, f.me())
        eq_("tests.contrib.test.me", func_name(f.me))
        eq_(3, f.add(1,2))
        eq_("tests.contrib.test.add", func_name(f.add))
        eq_(42, f.answer())
        eq_("tests.contrib.test.answer", func_name(f.answer))
