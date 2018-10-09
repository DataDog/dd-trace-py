import mock
import warnings

from unittest import TestCase
from nose.tools import eq_, ok_

from tests.test_tracer import get_dummy_tracer
from ddtrace.api import _parse_response_json, API
from ddtrace.compat import iteritems, httplib

class ResponseMock:
    def __init__(self, content):
        self.content = content

    def read(self):
        return self.content

class APITests(TestCase):

    def setUp(self):
        # DEV: Mock here instead of in tests, before we have patched `httplib.HTTPConnection`
        self.conn = mock.MagicMock(spec=httplib.HTTPConnection)
        self.api = API('localhost', 8126)

    def tearDown(self):
        del self.api
        del self.conn

    @mock.patch('logging.Logger.debug')
    def test_parse_response_json(self, log):
        tracer = get_dummy_tracer()
        tracer.debug_logging = True
        test_cases = {'OK': {'js': None, 'log': "please make sure trace-agent is up to date"},
                      'OK\n': {'js': None, 'log': "please make sure trace-agent is up to date"},
                      'error:unsupported-endpoint': {'js': None, 'log': "unable to load JSON 'error:unsupported-endpoint'"},
                      42: {'js': None, 'log': "unable to load JSON '42'"}, # int as key to trigger TypeError
                      '{}': {'js': {}},
                      '[]': {'js': []},
                      '{"rate_by_service": {"service:,env:":0.5, "service:mcnulty,env:test":0.9, "service:postgres,env:test":0.6}}':
                      {'js': {"rate_by_service":
                              {"service:,env:":0.5,
                               "service:mcnulty,env:test":0.9,
                               "service:postgres,env:test":0.6}}},
                      ' [4,2,1] ': {'js': [4,2,1]}}

        for k,v in iteritems(test_cases):
            r = ResponseMock(k)
            js =_parse_response_json(r)
            eq_(v['js'], js)
            if 'log' in v:
                ok_(1<=len(log.call_args_list), "not enough elements in call_args_list: %s" % log.call_args_list)
                l = log.call_args_list[-1][0][0]
                ok_(v['log'] in l, "unable to find %s in %s" % (v['log'], l))

    @mock.patch('ddtrace.compat.httplib.HTTPConnection')
    def test_put_connection_close(self, HTTPConnection):
        """
        When calling API._put
            we close the HTTPConnection we create
        """
        HTTPConnection.return_value = self.conn

        with warnings.catch_warnings(record=True) as w:
            self.api._put('/test', '<test data>', 1)

            self.assertEqual(len(w), 0, 'Test raised unexpected warnings: {0!r}'.format(w))

        self.conn.request.assert_called_once()
        self.conn.close.assert_called_once()

    @mock.patch('ddtrace.compat.httplib.HTTPConnection')
    def test_put_connection_close_exception(self, HTTPConnection):
        """
        When calling API._put raises an exception
            we close the HTTPConnection we create
        """
        HTTPConnection.return_value = self.conn
        # Ensure calling `request` raises an exception
        self.conn.request.side_effect = Exception

        with warnings.catch_warnings(record=True) as w:
            with self.assertRaises(Exception):
                self.api._put('/test', '<test data>', 1)

            self.assertEqual(len(w), 0, 'Test raised unexpected warnings: {0!r}'.format(w))

        self.conn.request.assert_called_once()
        self.conn.close.assert_called_once()
