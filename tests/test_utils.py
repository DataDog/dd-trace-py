import os
import unittest
import warnings

from nose.tools import eq_, ok_

from ddtrace.utils.deprecation import deprecation, deprecated, format_message
from ddtrace.utils.formats import asbool, get_env


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

    def test_get_env(self):
        # ensure `get_env` returns a default value if environment variables
        # are not set
        value = get_env('django', 'distributed_tracing')
        ok_(value is None)
        value = get_env('django', 'distributed_tracing', False)
        ok_(value is False)

    def test_get_env_found(self):
        # ensure `get_env` returns a value if the environment variable is set
        os.environ['DD_REQUESTS_DISTRIBUTED_TRACING'] = '1'
        value = get_env('requests', 'distributed_tracing')
        eq_(value, '1')

    def test_get_env_found_legacy(self):
        # ensure `get_env` returns a value if legacy environment variables
        # are used, raising a Deprecation warning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            os.environ['DATADOG_REQUESTS_DISTRIBUTED_TRACING'] = '1'
            value = get_env('requests', 'distributed_tracing')
            eq_(value, '1')
            ok_(len(w) == 1)
            ok_(issubclass(w[-1].category, DeprecationWarning))
            ok_('Use `DD_` prefix instead' in str(w[-1].message))

    def test_get_env_key_priority(self):
        # ensure `get_env` use `DD_` with highest priority
        os.environ['DD_REQUESTS_DISTRIBUTED_TRACING'] = 'highest'
        os.environ['DATADOG_REQUESTS_DISTRIBUTED_TRACING'] = 'lowest'
        value = get_env('requests', 'distributed_tracing')
        eq_(value, 'highest')

    def test_deprecation_formatter(self):
        # ensure the formatter returns the proper message
        msg = format_message(
            'deprecated_function',
            'use something else instead',
            '1.0.0',
        )
        expected = (
            '\'deprecated_function\' is deprecated and will be remove in future versions (1.0.0). '
            'use something else instead'
        )
        eq_(msg, expected)

    def test_deprecation(self):
        # ensure `deprecation` properly raise a DeprecationWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            deprecation(
                name='fn',
                message='message',
                version='1.0.0'
            )
            ok_(len(w) == 1)
            ok_(issubclass(w[-1].category, DeprecationWarning))
            ok_('message' in str(w[-1].message))

    def test_deprecated_decorator(self):
        # ensure `deprecated` decorator properly raise a DeprecationWarning
        @deprecated('decorator', version='1.0.0')
        def fxn():
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            fxn()
            ok_(len(w) == 1)
            ok_(issubclass(w[-1].category, DeprecationWarning))
            ok_('decorator' in str(w[-1].message))
