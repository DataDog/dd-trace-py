import os
import unittest

from nose.tools import eq_, ok_

from ddtrace.util import asbool, get_env


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
        # are used
        os.environ['DATADOG_REQUESTS_DISTRIBUTED_TRACING'] = '1'
        value = get_env('requests', 'distributed_tracing')
        eq_(value, '1')

    def test_get_env_key_priority(self):
        # ensure `get_env` use `DD_` with highest priority
        os.environ['DD_REQUESTS_DISTRIBUTED_TRACING'] = 'highest'
        os.environ['DATADOG_REQUESTS_DISTRIBUTED_TRACING'] = 'lowest'
        value = get_env('requests', 'distributed_tracing')
        eq_(value, 'highest')
