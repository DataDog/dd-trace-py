import os.path
import unittest

# 3rd party
from nose.tools import eq_
from mako.template import Template
from mako.lookup import TemplateLookup
from mako.runtime import Context

from ddtrace import Pin
from ddtrace.contrib.mako import patch, unpatch
from ddtrace.compat import StringIO, to_unicode
from tests.test_tracer import get_dummy_tracer

TEST_DIR = os.path.dirname(os.path.realpath(__file__))
TMPL_DIR = os.path.join(TEST_DIR, "templates")


class MakoTest(unittest.TestCase):
    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        Pin.override(Template, tracer=self.tracer)

    def tearDown(self):
        unpatch()

    def test_render(self):
        # render
        t = Template("Hello ${name}!")
        eq_(t.render(name="mako"), "Hello mako!")

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        for span in spans:
            eq_(span.service, "mako")
            eq_(span.span_type, "template")
            eq_(span.get_tag("mako.template_name"), "<memory>")

        eq_(spans[0].name, "mako.template.render")

        # render_unicode
        t = Template("Hello ${name}!")
        eq_(t.render_unicode(name="mako"), to_unicode("Hello mako!"))
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        eq_(spans[0].name, "mako.template.render_unicode")

        # render_context
        t = Template('Hello ${name}!')
        buf = StringIO()
        c = Context(buf, name='mako')
        t.render_context(c)
        eq_(buf.getvalue(), "Hello mako!")
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        eq_(spans[0].name, "mako.template.render_context")

    def test_file_template(self):
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template("template.html")
        eq_(t.render(name="mako"), "Hello mako!\n")

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        for span in spans:
            eq_(span.span_type, "template")
            eq_(span.service, "mako")
            eq_(span.get_tag("mako.template_name"), os.path.join(TMPL_DIR, "template.html"))
