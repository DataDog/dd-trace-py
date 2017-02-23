# -*- coding: utf-8 -*-
import unittest

from nose.tools import eq_

from ddtrace.contrib import func_name


class SomethingCallable(object):
    """
    A dummy class that implements __call__().
    """
    def __call__(self):
        return "something"


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
