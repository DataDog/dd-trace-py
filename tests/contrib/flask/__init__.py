import flask
from flask.testing import FlaskClient
import wrapt

from ddtrace.contrib.internal.flask.patch import patch
from ddtrace.contrib.internal.flask.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import TracerTestCase


class DDFlaskTestClient(FlaskClient):
    def __init__(self, *args, **kwargs):
        super(DDFlaskTestClient, self).__init__(*args, **kwargs)

    def open(self, *args, **kwargs):
        # From pep-333: If an iterable returned by the application has a close() method,
        # the server or gateway must call that method upon completion of the current request.
        # FlaskClient does not align with this specification so we must do this manually.
        # Closing the application iterable will finish the flask.request and flask.response
        # spans.
        res = super(DDFlaskTestClient, self).open(*args, **kwargs)
        res.make_sequence()
        if hasattr(res, "close"):
            # Note - werkzeug>=2.0 (used in flask>=2.0) calls response.close() for non streamed responses:
            # https://github.com/pallets/werkzeug/blame/b1911cd0a054f92fa83302cdb520d19449c0b87b/src/werkzeug/test.py#L1114
            res.close()
        return res


class BaseFlaskTestCase(TracerTestCase):
    def setUp(self):
        super(BaseFlaskTestCase, self).setUp()

        patch()

        self.app = flask.Flask(__name__, template_folder="test_templates/")
        self.app.test_client_class = DDFlaskTestClient
        self.client = self.app.test_client()
        Pin._override(self.app, tracer=self.tracer)

    def tearDown(self):
        super(BaseFlaskTestCase, self).tearDown()
        # Unpatch Flask
        unpatch()

    def get_spans(self):
        return self.tracer.pop()

    def assert_is_wrapped(self, obj):
        self.assertTrue(isinstance(obj, wrapt.ObjectProxy), "{} is not wrapped".format(obj))

    def assert_is_not_wrapped(self, obj):
        self.assertFalse(isinstance(obj, wrapt.ObjectProxy), "{} is wrapped".format(obj))

    def find_span_by_name(self, spans, name, required=True):
        """Helper to find the first span with a given name from a list"""
        span = next((s for s in spans if s.name == name), None)
        if required:
            self.assertIsNotNone(span, "could not find span with name {}".format(name))
        return span

    def find_span_parent(self, spans, span, required=True):
        """Helper to search for a span's parent in a given list of spans"""
        parent = next((s for s in spans if s.span_id == span.parent_id), None)
        if required:
            self.assertIsNotNone(parent, "could not find parent span {}".format(span))
        return parent
