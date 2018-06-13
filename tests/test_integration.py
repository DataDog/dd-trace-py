import os
import json
import time
import msgpack
import logging
import mock
import ddtrace

from unittest import TestCase, skip, skipUnless
from nose.tools import eq_, ok_

from ddtrace.api import API
from ddtrace.ext import http
from ddtrace.filters import FilterRequestsOnUrl
from ddtrace.constants import FILTERS_KEY
from ddtrace.span import Span
from ddtrace.tracer import Tracer
from ddtrace.encoding import JSONEncoder, MsgpackEncoder, get_encoder
from ddtrace.compat import httplib, PYTHON_INTERPRETER, PYTHON_VERSION
from tests.test_tracer import get_dummy_tracer


class MockedLogHandler(logging.Handler):
    """Record log messages to verify error logging logic"""

    def __init__(self, *args, **kwargs):
        self.messages = {'debug': [], 'info': [], 'warning': [], 'error': [], 'critical': []}
        super(MockedLogHandler, self).__init__(*args, **kwargs)

    def emit(self, record):
        self.acquire()
        try:
            self.messages[record.levelname.lower()].append(record.getMessage())
        finally:
            self.release()


class FlawedAPI(API):
    """
    Deliberately report data with an incorrect method to trigger a 4xx response
    """
    def _put(self, endpoint, data, count=0):
        conn = httplib.HTTPConnection(self.hostname, self.port)
        conn.request('HEAD', endpoint, data, self._headers)
        return conn.getresponse()


@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)


class TestWorkers(TestCase):
    """
    Ensures that a workers interacts correctly with the main thread. These are part
    of integration tests so real calls are triggered.
    """
    def _decode(self, payload):
        """
        Helper function that decodes data based on the given Encoder.
        """
        if isinstance(self.api._encoder, JSONEncoder):
            return json.loads(payload)
        elif isinstance(self.api._encoder, MsgpackEncoder):
            return msgpack.unpackb(payload, encoding='utf-8')

    def setUp(self):
        """
        Create a tracer with running workers, while spying the ``_put()`` method to
        keep trace of triggered API calls.
        """
        # create a new tracer
        self.tracer = Tracer()
        # spy the send() method
        self.api = self.tracer.writer.api
        self.api._put = mock.Mock(self.api._put, wraps=self.api._put)

    def tearDown(self):
        """
        Stop running worker
        """
        self.tracer.writer._worker.stop()

    def _wait_thread_flush(self):
        """
        Helper that waits for the thread flush
        """
        self.tracer.writer._worker.stop()
        self.tracer.writer._worker.join()

    def _get_endpoint_payload(self, calls, endpoint):
        """
        Helper to retrieve the endpoint call from a concurrent
        trace or service call.
        """
        for call, _ in calls:
            if endpoint in call[0]:
                return call[0], self._decode(call[1])

        return None, None

    def test_worker_single_trace(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace('client.testing').finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/traces')
        eq_(endpoint, '/v0.3/traces')
        eq_(len(payload), 1)
        eq_(len(payload[0]), 1)
        eq_(payload[0][0]['name'], 'client.testing')

    def test_worker_multiple_traces(self):
        # make a single send() if multiple traces are created before the flush interval
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        tracer.trace('client.testing').finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/traces')
        eq_(endpoint, '/v0.3/traces')
        eq_(len(payload), 2)
        eq_(len(payload[0]), 1)
        eq_(len(payload[1]), 1)
        eq_(payload[0][0]['name'], 'client.testing')
        eq_(payload[1][0]['name'], 'client.testing')

    def test_worker_single_trace_multiple_spans(self):
        # make a single send() if a single trace with multiple spans is created before the flush
        tracer = self.tracer
        parent = tracer.trace('client.testing')
        child = tracer.trace('client.testing').finish()
        parent.finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/traces')
        eq_(endpoint, '/v0.3/traces')
        eq_(len(payload), 1)
        eq_(len(payload[0]), 2)
        eq_(payload[0][0]['name'], 'client.testing')
        eq_(payload[0][1]['name'], 'client.testing')

    def test_worker_single_service(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('client.service', 'django', 'web')
        tracer.trace('client.testing').finish()

        # expect a call for traces and services
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 2)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/services')
        eq_(endpoint, '/v0.3/services')
        eq_(len(payload.keys()), 1)
        eq_(payload['client.service'], {'app': 'django', 'app_type': 'web'})

    def test_worker_service_called_multiple_times(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('backend', 'django', 'web')
        tracer.set_service_info('database', 'postgres', 'db')
        tracer.trace('client.testing').finish()

        # expect a call for traces and services
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 2)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/services')
        eq_(endpoint, '/v0.3/services')
        eq_(len(payload.keys()), 2)
        eq_(payload['backend'], {'app': 'django', 'app_type': 'web'})
        eq_(payload['database'], {'app': 'postgres', 'app_type': 'db'})

    def test_worker_http_error_logging(self):
        # Tests the logging http error logic
        tracer = self.tracer
        self.tracer.writer.api = FlawedAPI(Tracer.DEFAULT_HOSTNAME, Tracer.DEFAULT_PORT)
        tracer.trace('client.testing').finish()

        log = logging.getLogger("ddtrace.writer")
        log_handler = MockedLogHandler(level='DEBUG')
        log.addHandler(log_handler)

        # sleeping 1.01 secs to prevent writer from exiting before logging
        time.sleep(1.01)
        self._wait_thread_flush()
        assert tracer.writer._worker._last_error_ts < time.time()

        logged_errors = log_handler.messages['error']
        eq_(len(logged_errors), 1)
        ok_('failed_to_send traces to Agent: HTTP error status 400, reason Bad Request, message Content-Type:'
            in logged_errors[0])

    def test_worker_filter_request(self):
        self.tracer.configure(settings={FILTERS_KEY: [FilterRequestsOnUrl(r'http://example\.com/health')]})
        # spy the send() method
        self.api = self.tracer.writer.api
        self.api._put = mock.Mock(self.api._put, wraps=self.api._put)

        span = self.tracer.trace('testing.filteredurl')
        span.set_tag(http.URL, 'http://example.com/health')
        span.finish()
        span = self.tracer.trace('testing.nonfilteredurl')
        span.set_tag(http.URL, 'http://example.com/api/resource')
        span.finish()
        self._wait_thread_flush()

        # Only the second trace should have been sent
        eq_(self.api._put.call_count, 1)
        # check and retrieve the right call
        endpoint, payload = self._get_endpoint_payload(self.api._put.call_args_list, '/v0.3/traces')
        eq_(endpoint, '/v0.3/traces')
        eq_(len(payload), 1)
        eq_(payload[0][0]['name'], 'testing.nonfilteredurl')

@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestAPITransport(TestCase):
    """
    Ensures that traces are properly sent to a local agent. These are part
    of integration tests so real calls are triggered and you have to execute
    a real trace-agent to let them pass.
    """
    def setUp(self):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # create a new API object to test the transport using synchronous calls
        self.tracer = get_dummy_tracer()
        self.api_json = API('localhost', 8126, encoder=JSONEncoder())
        self.api_msgpack = API('localhost', 8126, encoder=MsgpackEncoder())

    @mock.patch('ddtrace.api.httplib.HTTPConnection')
    def test_send_presampler_headers(self, mocked_http):
        # register a single trace with a span and send them to the trace agent
        self.tracer.trace('client.testing').finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # make a call and retrieve the `conn` Mock object
        response = self.api_msgpack.send_traces(traces)
        request_call = mocked_http.return_value.request
        eq_(request_call.call_count, 1)

        # retrieve the headers from the mocked request call
        expected_headers = {
                'Datadog-Meta-Lang': 'python',
                'Datadog-Meta-Lang-Interpreter': PYTHON_INTERPRETER,
                'Datadog-Meta-Lang-Version': PYTHON_VERSION,
                'Datadog-Meta-Tracer-Version': ddtrace.__version__,
                'X-Datadog-Trace-Count': '1',
                'Content-Type': 'application/msgpack'
        }
        params, _ = request_call.call_args_list[0]
        headers = params[3]
        eq_(len(expected_headers), len(headers))
        for k, v in expected_headers.items():
            eq_(v, headers[k])

    @mock.patch('ddtrace.api.httplib.HTTPConnection')
    def test_send_presampler_headers_not_in_services(self, mocked_http):
        # register some services and send them to the trace agent
        services = [{
            'client.service': {
                'app': 'django',
                'app_type': 'web',
            },
        }]

        # make a call and retrieve the `conn` Mock object
        response = self.api_msgpack.send_services(services)
        request_call = mocked_http.return_value.request
        eq_(request_call.call_count, 1)

        # retrieve the headers from the mocked request call
        expected_headers = {
                'Datadog-Meta-Lang': 'python',
                'Datadog-Meta-Lang-Interpreter': PYTHON_INTERPRETER,
                'Datadog-Meta-Lang-Version': PYTHON_VERSION,
                'Datadog-Meta-Tracer-Version': ddtrace.__version__,
                'Content-Type': 'application/msgpack'
        }
        params, _ = request_call.call_args_list[0]
        headers = params[3]
        eq_(len(expected_headers), len(headers))
        for k, v in expected_headers.items():
            eq_(v, headers[k])

        # retrieve the headers from the mocked request call
        params, _ = request_call.call_args_list[0]
        headers = params[3]
        ok_('X-Datadog-Trace-Count' not in headers.keys())

    def test_send_single_trace(self):
        # register a single trace with a span and send them to the trace agent
        self.tracer.trace('client.testing').finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_with_wrong_errors(self):
        # if the error field is set to True, it must be cast as int so
        # that the agent decoder handles that properly without providing
        # a decoding error
        span = self.tracer.trace('client.testing')
        span.error = True
        span.finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_multiple_traces(self):
        # register some traces and send them to the trace agent
        self.tracer.trace('client.testing').finish()
        trace_1 = self.tracer.writer.pop()
        self.tracer.trace('client.testing').finish()
        trace_2 = self.tracer.writer.pop()
        traces = [trace_1, trace_2]

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_trace_multiple_spans(self):
        # register some traces and send them to the trace agent
        with self.tracer.trace('client.testing'):
            self.tracer.trace('client.testing').finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_multiple_traces_multiple_spans(self):
        # register some traces and send them to the trace agent
        with self.tracer.trace('client.testing'):
            self.tracer.trace('client.testing').finish()
        trace_1 = self.tracer.writer.pop()

        with self.tracer.trace('client.testing'):
            self.tracer.trace('client.testing').finish()
        trace_2 = self.tracer.writer.pop()

        traces = [trace_1, trace_2]

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_service(self):
        # register some services and send them to the trace agent
        services = [{
            'client.service': {
                'app': 'django',
                'app_type': 'web',
            },
        }]

        # test JSON encoder
        response = self.api_json.send_services(services)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_services(services)
        ok_(response)
        eq_(response.status, 200)

    def test_send_service_called_multiple_times(self):
        # register some services and send them to the trace agent
        services = [{
            'backend': {
                'app': 'django',
                'app_type': 'web',
            },
            'database': {
                'app': 'postgres',
                'app_type': 'db',
            },
        }]

        # test JSON encoder
        response = self.api_json.send_services(services)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_services(services)
        ok_(response)
        eq_(response.status, 200)

@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestAPIDowngrade(TestCase):
    """
    Ensures that if the tracing client found an earlier trace agent,
    it will downgrade the current connection to a stable API version
    """
    @skip('msgpack package split breaks this test; it works for newer version of msgpack')
    def test_get_encoder_default(self):
        # get_encoder should return MsgpackEncoder instance if
        # msgpack and the CPP implementaiton are available
        encoder = get_encoder()
        ok_(isinstance(encoder, MsgpackEncoder))

    @mock.patch('ddtrace.encoding.MSGPACK_ENCODING', False)
    def test_get_encoder_fallback(self):
        # get_encoder should return JSONEncoder instance if
        # msgpack or the CPP implementaiton, are not available
        encoder = get_encoder()
        ok_(isinstance(encoder, JSONEncoder))

    @skip('msgpack package split breaks this test; it works for newer version of msgpack')
    def test_downgrade_api(self):
        # make a call to a not existing endpoint, downgrades
        # the current API to a stable one
        tracer = get_dummy_tracer()
        tracer.trace('client.testing').finish()
        trace = tracer.writer.pop()

        # the encoder is right but we're targeting an API
        # endpoint that is not available
        api = API('localhost', 8126)
        api._traces = '/v0.0/traces'
        ok_(isinstance(api._encoder, MsgpackEncoder))

        # after the call, we downgrade to a working endpoint
        response = api.send_traces([trace])
        ok_(response)
        eq_(response.status, 200)
        ok_(isinstance(api._encoder, JSONEncoder))

@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestRateByService(TestCase):
    """
    Check we get feedback from the agent and we're able to process it.
    """
    def setUp(self):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # create a new API object to test the transport using synchronous calls
        self.tracer = get_dummy_tracer()
        self.api_json = API('localhost', 8126, encoder=JSONEncoder())
        self.api_msgpack = API('localhost', 8126, encoder=MsgpackEncoder())

    def test_send_single_trace(self):
        # register a single trace with a span and send them to the trace agent
        self.tracer.trace('client.testing').finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # [TODO:christian] when CI has an agent that is able to process the v0.4
        # endpoint, add a check to:
        # - make sure the output is a valid JSON
        # - make sure the priority sampler (if enabled) is updated

        # test JSON encoder
        response = self.api_json.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

        # test Msgpack encoder
        response = self.api_msgpack.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

@skipUnless(
    os.environ.get('TEST_DATADOG_INTEGRATION', False),
    'You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable'
)
class TestConfigure(TestCase):
    """
    Ensures that when calling configure without specifying hostname and port,
    previous overrides have been kept.
    """
    def test_configure_keeps_api_hostname_and_port(self):
        tracer = Tracer() # use real tracer with real api
        eq_('localhost', tracer.writer.api.hostname)
        eq_(8126, tracer.writer.api.port)
        tracer.configure(hostname='127.0.0.1', port=8127)
        eq_('127.0.0.1', tracer.writer.api.hostname)
        eq_(8127, tracer.writer.api.port)
        tracer.configure(priority_sampling=True)
        eq_('127.0.0.1', tracer.writer.api.hostname)
        eq_(8127, tracer.writer.api.port)
