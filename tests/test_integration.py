import os
import json
import mock
import time
import msgpack

from unittest import TestCase, skipUnless
from nose.tools import eq_, ok_

from ddtrace.api import API
from ddtrace.span import Span
from ddtrace.tracer import Tracer
from ddtrace.encoding import JSONEncoder, MsgpackEncoder, get_encoder
from tests.test_tracer import get_dummy_tracer


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

    def test_worker_single_trace(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace('client.testing').finish()

        # one send is expected
        self._wait_thread_flush()
        eq_(self.api._put.call_count, 1)
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = self._decode(self.api._put.call_args[0][1])
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
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = self._decode(self.api._put.call_args[0][1])
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
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = self._decode(self.api._put.call_args[0][1])
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
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = self._decode(self.api._put.call_args[0][1])
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
        # check arguments
        endpoint = self.api._put.call_args[0][0]
        payload = self._decode(self.api._put.call_args[0][1])
        eq_(endpoint, '/v0.3/services')
        eq_(len(payload.keys()), 2)
        eq_(payload['backend'], {'app': 'django', 'app_type': 'web'})
        eq_(payload['database'], {'app': 'postgres', 'app_type': 'db'})


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
        self.api_json = API('localhost', 7777, encoder=JSONEncoder())
        self.api_msgpack = API('localhost', 7777, encoder=MsgpackEncoder())

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

    def test_downgrade_api(self):
        # make a call to a not existing endpoint, downgrades
        # the current API to a stable one
        tracer = get_dummy_tracer()
        tracer.trace('client.testing').finish()
        trace = tracer.writer.pop()

        # the encoder is right but we're targeting an API
        # endpoint that is not available
        api = API('localhost', 7777)
        api._traces = '/v0.0/traces'
        ok_(isinstance(api._encoder, MsgpackEncoder))

        # after the call, we downgrade to a working endpoint
        response = api.send_traces([trace])
        ok_(response)
        eq_(response.status, 200)
        ok_(isinstance(api._encoder, JSONEncoder))
