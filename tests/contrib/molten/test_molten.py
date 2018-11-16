from unittest import TestCase

import molten
from molten.testing import TestClient

from ddtrace import Pin
from ddtrace.contrib.molten import patch, unpatch
from nose.tools import eq_, ok_

import pprint
import inspect

from ...test_tracer import get_dummy_tracer


# NOTE: Type annotations required by molten otherwise parameters cannot be coerced
def hello(name: str, age: int) -> str:
    return f'Hello {age} year old named {name}!'

def test_molten_client():
    app = molten.App(routes=[molten.Route('/hello/{name}/{age}', hello)])
    client = TestClient(app)
    uri = app.reverse_uri('hello', name='Jim', age=24)
    return client.get(uri)

class TestMolten(TestCase):
    """"Ensures Molten is properly instrumented."""

    TEST_SERVICE = 'molten-patch'

    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        self.pin = Pin(service=self.TEST_SERVICE, tracer=self.tracer)
        Pin.override(molten, tracer=self.tracer)

    def tearDown(self):
        unpatch()

    def _test_route_success(self):
        client, tracer = self.get_client_and_tracer()
        uri = client.app.reverse_uri('hello', name='Jim', age=24)
        response = client.get(uri)
        spans = tracer.writer.pop()
        eq_(response.status_code, 200)
        eq_(response.json(), 'Hello 24 year old named Jim!')
        # pp = pprint.PrettyPrinter(indent=4)
        # pp.pprint([
        #     s.to_dict()
        #     for s in spans
        # ])
        eq_(len(spans), 18)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'molten.request')
        eq_(span.resource, 'GET /hello/Jim/24')
        eq_(span.get_tag('http.url'), uri)
        eq_(span.get_tag('http.method'), 'GET')
        eq_(span.get_tag('http.status_code'), '200')

    def _test_distributed_tracing(self):
        def prepare_environ(environ):
            environ.update({
                'DATADOG_MOLTEN_DISTRIBUTED_TRACING': 'True',
                'HTTP_X_DATADOG_TRACE_ID': '100',
                'HTTP_X_DATADOG_PARENT_ID': '42',
            })
            return environ

        client, tracer = self.get_client_and_tracer()
        uri = client.app.reverse_uri('hello', name='Jim', age=24)
        response = client.get(uri, prepare_environ=prepare_environ)
        eq_(response.status_code, 200)
        eq_(response.json(), 'Hello 24 year old named Jim!')
        spans = tracer.writer.pop()
        # pp = pprint.PrettyPrinter(indent=4)
        # pp.pprint([
        #     s.to_dict()
        #     for s in spans
        # ])
        eq_(len(spans), 18)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'molten.request')
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        spans = tracer.writer.pop()

    def test_unpatch_patch(self):
        unpatch()
        patch()
        ok_(Pin.get_from(molten) is not None)
        test_molten_client()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 18)

    def _test_patch_unpatch(self):
        # Already patched in setUp
        ok_(Pin.get_from(molten) is not None)
        test_molten_client()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 18)

        # Test unpatch
        unpatch()
        ok_(Pin.get_from(molten) is None)
        test_molten_client()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def _test_patch_idempotence(self):
        # Patch multiple times
        patch()
        patch()
        test_molten_client()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 18)
