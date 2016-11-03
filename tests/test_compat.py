# -*- coding: utf-8 -*-
# Define source file encoding to support raw unicode characters in Python 2

# Third party
from nose.tools import eq_

# Project
from ddtrace.compat import to_unicode, PY2


# Use different test suites for each Python version, this allows us to test the expected
#   results for each Python version rather than writing a generic "works for both" test suite
if PY2:
    class TestCompatPY2(object):

        def test_to_unicode_string(self):
            # Calling `compat.to_unicode` on a non-unicode string
            res = to_unicode('test')
            eq_(type(res), unicode)
            eq_(res, 'test')

        def test_to_unicode_unicode_encoded(self):
            # Calling `compat.to_unicode` on a unicode encoded string
            res = to_unicode('\xc3\xbf')
            eq_(type(res), unicode)
            eq_(res, u'ÿ')

        def test_to_unicode_unicode_double_decode(self):
            # Calling `compat.to_unicode` on a unicode decoded string
            # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
            #   `'\xc3\xbf'.decode('utf-8').decode('utf-8')`
            res = to_unicode('\xc3\xbf'.decode('utf-8'))
            eq_(type(res), unicode)
            eq_(res, u'ÿ')

        def test_to_unicode_unicode_string(self):
            # Calling `compat.to_unicode` on a unicode string
            res = to_unicode(u'ÿ')
            eq_(type(res), unicode)
            eq_(res, u'ÿ')

        def test_to_unicode_bytearray(self):
            # Calling `compat.to_unicode` with a `bytearray` containing unicode
            res = to_unicode(bytearray('\xc3\xbf'))
            eq_(type(res), unicode)
            eq_(res, u'ÿ')

        def test_to_unicode_bytearray_double_decode(self):
            #  Calling `compat.to_unicode` with an already decoded `bytearray`
            # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
            #   `bytearray('\xc3\xbf').decode('utf-8').decode('utf-8')`
            res = to_unicode(bytearray('\xc3\xbf').decode('utf-8'))
            eq_(type(res), unicode)
            eq_(res, u'ÿ')

        def test_to_unicode_non_string(self):
            #  Calling `compat.to_unicode` on non-string types
            eq_(to_unicode(1), u'1')
            eq_(to_unicode(True), u'True')
            eq_(to_unicode(None), u'None')
            eq_(to_unicode(dict(key='value')), u'{\'key\': \'value\'}')

else:
    class TestCompatPY3(object):
        def test_to_unicode_string(self):
            # Calling `compat.to_unicode` on a non-unicode string
            res = to_unicode('test')
            eq_(type(res), str)
            eq_(res, 'test')

        def test_to_unicode_unicode_encoded(self):
            # Calling `compat.to_unicode` on a unicode encoded string
            res = to_unicode('\xff')
            eq_(type(res), str)
            eq_(res, 'ÿ')

        def test_to_unicode_unicode_string(self):
            # Calling `compat.to_unicode` on a unicode string
            res = to_unicode('ÿ')
            eq_(type(res), str)
            eq_(res, 'ÿ')

        def test_to_unicode_bytearray(self):
            # Calling `compat.to_unicode` with a `bytearray` containing unicode """
            res = to_unicode(bytearray('\xff', 'utf-8'))
            eq_(type(res), str)
            eq_(res, 'ÿ')

        def test_to_unicode_non_string(self):
            # Calling `compat.to_unicode` on non-string types
            eq_(to_unicode(1), '1')
            eq_(to_unicode(True), 'True')
            eq_(to_unicode(None), 'None')
            eq_(to_unicode(dict(key='value')), '{\'key\': \'value\'}')
