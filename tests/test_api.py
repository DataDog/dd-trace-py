import mock

from unittest import TestCase
from nose.tools import eq_, ok_

from tests.test_tracer import get_dummy_tracer
from ddtrace.api import _parse_response_json
from ddtrace.compat import iteritems

class ResponseMock:
    def __init__(self, content):
        self.content = content

    def read(self):
        return self.content

class APITests(TestCase):
    @mock.patch('logging.Logger.debug')
    def test_parse_response_json(self, log):
        tracer = get_dummy_tracer()
        tracer.debug_logging = True
        test_cases = {'OK': {'js': None, 'log': "please make sure trace-agent is up to date"},
                      'OK\n': {'js': None, 'log': "please make sure trace-agent is up to date"},
                      'error:unsupported-endpoint': {'js': None, 'log': "unable to load JSON 'error:unsupported-endpoint'"},
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
                print(log.call_args_list)
                l = log.call_args_list[-1][0][0]
                ok_(v['log'] in l, "unable to find %s in %s" % (v['log'], l))
