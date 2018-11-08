from unittest import TestCase

import molten
from ddtrace import Pin
from ddtrace.contrib.molten import patch, unpatch
from molten import App, Route
from molten.testing import TestClient
from nose.tools import eq_, ok_

from ...test_tracer import get_dummy_tracer


class TestMolten(TestCase):
    """"Ensures Molten is properly instrumented."""

    TEST_SERVICE = 'molten-patch'

    def setUp(self):
        self.tracer = get_dummy_tracer()
        # NOTE: Type annotations required by molten otherwise parameters cannot be coerced
        def hello(name: str, age: int) -> str:
            return f'Hello {age} year old named {name}!'
        self.app = App(routes=[Route('/hello/{name}/{age}', hello)])
        patch()

    def tearDown(self):
        unpatch()

    def test_route_success(self):
        client, tracer = self.get_client_and_tracer()
        uri = self.app.reverse_uri('hello', name='Jim', age=24)
        response = client.get(uri)
        eq_(response.status_code, 200)
        eq_(response.json(), 'Hello 24 year old named Jim!')
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'molten.request')
        eq_(span.resource, 'GET /hello/Jim/24')
        eq_(span.get_tag('http.url'), uri)
        eq_(span.get_tag('http.method'), 'GET')
        eq_(span.get_tag('http.status_code'), '200')

    def test_distributed_tracing(self):
        def prepare_environ(environ):
            environ.update({
                'DATADOG_MOLTEN_DISTRIBUTED_TRACING': 'True',
                'HTTP_X_DATADOG_TRACE_ID': '100',
                'HTTP_X_DATADOG_PARENT_ID': '42',
            })
            return environ

        client, tracer = self.get_client_and_tracer()
        uri = self.app.reverse_uri('hello', name='Jim', age=24)
        response = client.get(uri, prepare_environ=prepare_environ)
        eq_(response.status_code, 200)
        eq_(response.json(), 'Hello 24 year old named Jim!')
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'molten.request')
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)

    def get_client_and_tracer(self):
        tracer = get_dummy_tracer()
        client = TestClient(self.app)
        Pin.override(molten, service=self.TEST_SERVICE, tracer=tracer)
        return client, tracer

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        def simple_check():
            client, tracer = self.get_client_and_tracer()
            uri = self.app.reverse_uri('hello', name='Jim', age=24)
            _ = client.get(uri)
            return tracer.writer.pop()

        # Test patch idempotence
        patch()
        patch()
        eq_(len(simple_check()), 1)

        # Test unpatch
        unpatch()
        eq_(len(simple_check()), 0)

        # Test patch again
        patch()
        eq_(len(simple_check()), 1)
