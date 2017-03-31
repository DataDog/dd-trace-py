from nose.tools import eq_

from ddtrace.contrib.util import func_name


class SomethingCallable(object):
    """
    A dummy class that implements __call__().
    """
    value = 42

    def __call__(self):
        return 'something'

    def me(self):
        return self

    @staticmethod
    def add(a, b):
        return a + b

    @classmethod
    def answer(cls):
        return cls.value


def some_function():
    """
    A function doing nothing.
    """
    return 'nothing'


class TestContrib(object):
    """
    Ensure that contrib utility functions handles corner cases
    """
    def test_func_name(self):
        # check that func_name works on anything callable, not only funcs.
        eq_('nothing', some_function())
        eq_('tests.contrib.test_utils.some_function', func_name(some_function))

        f = SomethingCallable()
        eq_('something', f())
        eq_('tests.contrib.test_utils.SomethingCallable', func_name(f))

        eq_(f, f.me())
        eq_('tests.contrib.test_utils.me', func_name(f.me))
        eq_(3, f.add(1,2))
        eq_('tests.contrib.test_utils.add', func_name(f.add))
        eq_(42, f.answer())
        eq_('tests.contrib.test_utils.answer', func_name(f.answer))
