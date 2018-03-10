import unittest

from nose.tools import eq_

from ddtrace.util import asbool


class TestUtilities(unittest.TestCase):
    def test_asbool(self):
        # ensure the value is properly cast
        eq_(asbool("True"), True)
        eq_(asbool("true"), True)
        eq_(asbool("1"), True)
        eq_(asbool("False"), False)
        eq_(asbool("false"), False)
        eq_(asbool(None), False)
        eq_(asbool(""), False)
        eq_(asbool(True), True)
        eq_(asbool(False), False)
