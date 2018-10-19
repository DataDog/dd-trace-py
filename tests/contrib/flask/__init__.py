import unittest

from ddtrace import Pin
from ddtrace.contrib.flask import patch, unpatch
import flask

from ...test_tracer import get_dummy_tracer


class BaseFlaskTestCase(unittest.TestCase):
    def setUp(self):
        patch()

        self.tracer = get_dummy_tracer()
        self.app = flask.Flask(__name__)
        self.client = self.app.test_client()
        Pin.override(self.app, tracer=self.tracer)

    def tearDown(self):
        # Remove any remaining spans
        self.tracer.writer.pop()

        # Unpatch Flask
        unpatch()

    def get_spans(self):
        return self.tracer.writer.pop()
