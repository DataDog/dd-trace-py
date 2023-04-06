import openai
import os

from ddtrace.contrib.openai.patch import patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.pin import Pin
from tests.utils import TracerTestCase


class TestOpenAI(TracerTestCase):
    def setUp(self):
        super(TestOpenAI, self).setUp()
        patch()
        Pin.override(openai, tracer=self.tracer)

    def tearDown(self):
        super(TestOpenAI, self).tearDown()
        unpatch()

    def test_completion(self):
        # create a completion (expected to fail since api key is not set)
        openai.Completion.create(model="ada", prompt="Hello world")
        spans = self.pop_spans()
        assert len(spans) > 0
        tags = spans[0].get_tags()
        assert tags["model"] == "ada"
        assert tags["prompt"] == "Hello world"
