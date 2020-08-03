import os.path

# 3rd party
from mako.template import Template
from mako.lookup import TemplateLookup
from mako.runtime import Context

from ddtrace import Pin
from ddtrace.contrib.mako import patch, unpatch
from ddtrace.compat import StringIO, to_unicode
from tests.tracer.test_tracer import get_dummy_tracer
from ...base import BaseTracerTestCase
from ...utils import assert_is_measured

TEST_DIR = os.path.dirname(os.path.realpath(__file__))
TMPL_DIR = os.path.join(TEST_DIR, 'templates')


class MakoTest(BaseTracerTestCase):
    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        Pin.override(Template, tracer=self.tracer)

    def tearDown(self):
        unpatch()

    def test_render(self):
        # render
        t = Template('Hello ${name}!')
        self.assertEqual(t.render(name='mako'), 'Hello mako!')

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, 'mako')
        self.assertEqual(spans[0].span_type, 'template')
        self.assertEqual(spans[0].get_tag('mako.template_name'), '<memory>')
        self.assertEqual(spans[0].name, 'mako.template.render')
        self.assertEqual(spans[0].resource, '<memory>')

        # render_unicode
        t = Template('Hello ${name}!')
        self.assertEqual(t.render_unicode(name='mako'), to_unicode('Hello mako!'))
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, 'mako')
        self.assertEqual(spans[0].span_type, 'template')
        self.assertEqual(spans[0].get_tag('mako.template_name'), '<memory>')
        self.assertEqual(spans[0].name, 'mako.template.render_unicode')
        self.assertEqual(spans[0].resource, '<memory>')

        # render_context
        t = Template('Hello ${name}!')
        buf = StringIO()
        c = Context(buf, name='mako')
        t.render_context(c)
        self.assertEqual(buf.getvalue(), 'Hello mako!')
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, 'mako')
        self.assertEqual(spans[0].span_type, 'template')
        self.assertEqual(spans[0].get_tag('mako.template_name'), '<memory>')
        self.assertEqual(spans[0].name, 'mako.template.render_context')
        self.assertEqual(spans[0].resource, '<memory>')

    def test_file_template(self):
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template('template.html')
        self.assertEqual(t.render(name='mako'), 'Hello mako!\n')

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        template_name = os.path.join(TMPL_DIR, 'template.html')

        assert_is_measured(spans[0])
        self.assertEqual(spans[0].span_type, 'template')
        self.assertEqual(spans[0].service, 'mako')
        self.assertEqual(spans[0].get_tag('mako.template_name'), template_name)
        self.assertEqual(spans[0].name, 'mako.template.render')
        self.assertEqual(spans[0].resource, template_name)

    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a service name is specified by the user
            The mako integration should use it as the service name
        """
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template('template.html')
        self.assertEqual(t.render(name='mako'), 'Hello mako!\n')

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        assert spans[0].service == "mysvc"
