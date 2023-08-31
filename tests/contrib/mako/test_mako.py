import os.path

from mako.lookup import TemplateLookup
from mako.runtime import Context
from mako.template import Template

from ddtrace import Pin
from ddtrace.contrib.mako import patch
from ddtrace.contrib.mako import unpatch
from ddtrace.contrib.mako.constants import DEFAULT_TEMPLATE_NAME
from ddtrace.internal.compat import StringIO
from ddtrace.internal.compat import to_unicode
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


TEST_DIR = os.path.dirname(os.path.realpath(__file__))
TMPL_DIR = os.path.join(TEST_DIR, "templates")


class MakoTest(TracerTestCase):
    def setUp(self):
        super(MakoTest, self).setUp()
        patch()
        Pin.override(Template, tracer=self.tracer)

    def tearDown(self):
        super(MakoTest, self).tearDown()
        unpatch()

    def test_render(self):
        # render
        t = Template("Hello ${name}!")
        self.assertEqual(t.render(name="mako"), "Hello mako!")

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)

        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, "mako")
        self.assertEqual(spans[0].span_type, "template")
        self.assertEqual(spans[0].get_tag("mako.template_name"), DEFAULT_TEMPLATE_NAME)
        self.assertEqual(spans[0].get_tag("component"), "mako")
        self.assertEqual(spans[0].name, "mako.template.render")
        self.assertEqual(spans[0].resource, DEFAULT_TEMPLATE_NAME)

        # render_unicode
        t = Template("Hello ${name}!")
        self.assertEqual(t.render_unicode(name="mako"), to_unicode("Hello mako!"))
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, "mako")
        self.assertEqual(spans[0].span_type, "template")
        self.assertEqual(spans[0].get_tag("mako.template_name"), DEFAULT_TEMPLATE_NAME)
        self.assertEqual(spans[0].get_tag("component"), "mako")
        self.assertEqual(spans[0].name, "mako.template.render_unicode")
        self.assertEqual(spans[0].resource, DEFAULT_TEMPLATE_NAME)

        # render_context
        t = Template("Hello ${name}!")
        buf = StringIO()
        c = Context(buf, name="mako")
        t.render_context(c)
        self.assertEqual(buf.getvalue(), "Hello mako!")
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        assert_is_measured(spans[0])
        self.assertEqual(spans[0].service, "mako")
        self.assertEqual(spans[0].span_type, "template")
        self.assertEqual(spans[0].get_tag("mako.template_name"), DEFAULT_TEMPLATE_NAME)
        self.assertEqual(spans[0].get_tag("component"), "mako")
        self.assertEqual(spans[0].name, "mako.template.render_context")
        self.assertEqual(spans[0].resource, DEFAULT_TEMPLATE_NAME)

    def test_file_template(self):
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template("template.html")
        self.assertEqual(t.render(name="mako"), "Hello mako!\n")

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)

        template_name = os.path.join(TMPL_DIR, "template.html")

        assert_is_measured(spans[0])
        self.assertEqual(spans[0].span_type, "template")
        self.assertEqual(spans[0].service, "mako")
        self.assertEqual(spans[0].get_tag("mako.template_name"), template_name)
        self.assertEqual(spans[0].get_tag("component"), "mako")
        self.assertEqual(spans[0].name, "mako.template.render")
        self.assertEqual(spans[0].resource, template_name)

    def test_deftemplate(self):
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template("template.html")

        # Get a DefTemplate from `t.render_body()`
        dt = t.get_def("body")

        assert dt.render(name="DefTemplate") == "Hello DefTemplate!\n"

        spans = self.pop_spans()
        assert len(spans) == 1

        assert spans[0].resource == "template_html.render_body"
        assert spans[0].get_tag("mako.template_name") == "template_html.render_body"

    def _schema_test_spans(self):
        tmpl_lookup = TemplateLookup(directories=[TMPL_DIR])
        t = tmpl_lookup.get_template("template.html")
        self.assertEqual(t.render(name="mako"), "Hello mako!\n")

        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)

        return spans

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_service_name_default(self):
        """
        default/v0: When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == "mysvc", "Expected service name to be mysvc, got {}".format(spans[0].service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_service_name_v0(self):
        """
        default/v0: When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == "mysvc", "Expected service name to be mysvc, got {}".format(spans[0].service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_service_name_v1(self):
        """
        v1: When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == "mysvc", "Expected service name to be mysvc, got {}".format(spans[0].service)

    @TracerTestCase.run_in_subprocess()
    def test_schematized_unspecified_service_name_default(self):
        """
        When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == "mako", "Expected service name to be mysvc, got {}".format(spans[0].service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0(self):
        """
        v0/default: When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == "mako", "Expected service name to be mysvc, got {}".format(spans[0].service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1(self):
        """
        v1: When a service name is specified by the user
            The mako integration should use it as the service name
        """
        spans = self._schema_test_spans()
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME, "Expected service name to be mysvc, got {}".format(
            spans[0].service
        )
