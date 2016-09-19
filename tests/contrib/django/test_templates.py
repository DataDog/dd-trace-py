import time

# 3rd party
from nose.tools import eq_
from django.test import SimpleTestCase
from django.template import Context, Template

# project
from ddtrace.tracer import Tracer
from ddtrace.contrib.django.templates import patch_template

# testing
from ...test_tracer import DummyWriter


class TraceTemplateTest(SimpleTestCase):
    """
    Ensures that the template system is properly traced
    """
    def setUp(self):
        # create a tracer and patch the template
        tracer = Tracer()
        tracer.writer = DummyWriter()
        patch_template(tracer)
        self.tracer = tracer

    def tearDown(self):
        # unpatch the template system
        Template.render = Template._datadog_original_render
        del Template._datadog_original_render

    def test_template(self):
        # prepare a base template using the default engine
        template = Template("Hello {{name}}!")
        ctx = Context({'name': 'Django'})

        # (trace) the template rendering
        start = time.time()
        eq_(template.render(ctx), 'Hello Django!')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.span_type, 'template')
        eq_(span.name, 'django.template')
        eq_(span.get_tag('django.template_name'), 'unknown')
        assert start < span.start < span.start + span.duration < end
