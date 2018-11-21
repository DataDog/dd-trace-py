from unittest import TestCase

import molten
from molten.testing import TestClient

from ddtrace import Pin
from ddtrace.contrib.molten import patch, unpatch

from ...test_tracer import get_dummy_tracer

# NOTE: Type annotations required by molten otherwise parameters cannot be coerced
def hello(name: str, age: int) -> str:
    return f'Hello {age} year old named {name}!'

def molten_client(prepare_environ=None):
    app = molten.App(routes=[molten.Route('/hello/{name}/{age}', hello)])
    client = TestClient(app)
    uri = app.reverse_uri('hello', name='Jim', age=24)
    if prepare_environ:
        return client.get(uri, prepare_environ=prepare_environ)
    return client.get(uri)

MOLTEN_VERSION =  tuple(map(int, molten.__version__.split()[0].split('.')))

class TestMolten(TestCase):
    """"Ensures Molten is properly instrumented."""

    TEST_SERVICE = 'molten-patch'

    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        Pin.override(molten, tracer=self.tracer, service=self.TEST_SERVICE)

    def tearDown(self):
        unpatch()
        self.tracer.writer.pop()

    def test_route_success(self):
        response = molten_client()
        spans = self.tracer.writer.pop()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 'Hello 24 year old named Jim!')
        span = spans[0]
        self.assertEqual(span.service, self.TEST_SERVICE)
        self.assertEqual(span.name, 'molten.request')
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.url'), '/hello/Jim/24')
        self.assertEqual(span.get_tag('http.status_code'), '200')

    def test_resources(self):
        response = molten_client()
        spans = self.tracer.writer.pop()

        expected_resources = [
            'molten.app.__call__',
            'molten.middleware.ResponseRendererMiddleware',
            'molten.components.HeaderComponent',
            'molten.components.CookiesComponent',
            'molten.components.QueryParamComponent',
            'molten.components.RequestBodyComponent',
            'molten.components.RequestDataComponent',
            'molten.components.SchemaComponent',
            'molten.components.UploadedFileComponent',
            'molten.components.HeaderComponent',
            'molten.components.CookiesComponent',
            'molten.components.QueryParamComponent',
            'molten.components.RequestBodyComponent',
            'molten.components.RequestDataComponent',
            'molten.components.SchemaComponent',
            'molten.components.UploadedFileComponent',
            'GET /hello/{name}/{age}',
            'molten.renderers.JSONRenderer'
        ]

        if MOLTEN_VERSION >= (0, 7, 2):
            # Addition of `UploadedFileComponent`
            self.assertEqual([s.resource for s in spans],
                expected_resources)
        else:
            # `UploadedFileComponent` not supported
            self.assertEqual([s.resource for s in spans],
                [r for r in expected_resources if r != 'UploadedFileComponent'])

    def test_distributed_tracing(self):
        def prepare_environ(environ):
            environ.update({
                'DD_MOLTEN_DISTRIBUTED_TRACING': 'True',
                'HTTP_X_DATADOG_TRACE_ID': '100',
                'HTTP_X_DATADOG_PARENT_ID': '42',
            })
            return environ

        response = molten_client(prepare_environ=prepare_environ)
        spans = self.tracer.writer.pop()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 'Hello 24 year old named Jim!')
        span = spans[0]
        self.assertEqual(span.service, self.TEST_SERVICE)
        self.assertEqual(span.name, 'molten.request')
        self.assertEqual(span.trace_id, 100)
        self.assertEqual(span.parent_id, 42)

    def test_unpatch_patch(self):
        unpatch()
        self.assertTrue(Pin.get_from(molten) is None)
        molten_client()
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

        patch()
        Pin.override(molten, tracer=self.tracer)
        self.assertTrue(Pin.get_from(molten) is not None)
        molten_client()
        spans = self.tracer.writer.pop()
        self.assertTrue(len(spans) > 0)

    def test_patch_unpatch(self):
        # Already patched in setUp
        self.assertTrue(Pin.get_from(molten) is not None)
        molten_client()
        spans = self.tracer.writer.pop()
        self.assertTrue(len(spans) > 0)

        # Test unpatch
        unpatch()
        self.assertTrue(Pin.get_from(molten) is None)
        molten_client()
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

    def test_patch_idempotence(self):
        # Patch multiple times
        patch()
        molten_client()
        spans = self.tracer.writer.pop()
        self.assertTrue(len(spans) > 0)
