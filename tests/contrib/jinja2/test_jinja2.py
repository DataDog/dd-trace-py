# -*- coding: utf-8 -*-
import os.path

# 3rd party
import jinja2

from ddtrace import config
from ddtrace.contrib.internal.jinja2.patch import patch
from ddtrace.contrib.internal.jinja2.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured


TEST_DIR = os.path.dirname(os.path.realpath(__file__))
TMPL_DIR = os.path.join(TEST_DIR, "templates")


class Jinja2Test(TracerTestCase):
    def setUp(self):
        super(Jinja2Test, self).setUp()
        patch()
        # prevent cache effects when using Template('code...')
        try:
            jinja2.environment._spontaneous_environments.clear()
        except AttributeError:
            jinja2.utils.clear_caches()
        Pin._override(jinja2.environment.Environment, tracer=self.tracer)

    def tearDown(self):
        super(Jinja2Test, self).tearDown()
        # restore the tracer
        unpatch()

    def test_render_inline_template(self):
        t = jinja2.environment.Template("Hello {{name}}!")
        assert t.render(name="Jinja") == "Hello Jinja!"

        # tests
        spans = self.pop_spans()
        assert len(spans) == 2

        for span in spans:
            assert span.service == "tests.contrib.jinja2"
            assert span.span_type == "template"
            assert span.get_tag("jinja2.template_name") == "<memory>"
            assert span.get_tag("component") == "jinja2"

        assert spans[0].name == "jinja2.compile"
        assert_is_not_measured(spans[0])
        assert spans[1].name == "jinja2.render"
        assert_is_measured(spans[1])

    def test_generate_inline_template(self):
        t = jinja2.environment.Template("Hello {{name}}!")
        assert "".join(t.generate(name="Jinja")) == "Hello Jinja!"

        # tests
        spans = self.pop_spans()
        assert len(spans) == 2

        for span in spans:
            assert span.service == "tests.contrib.jinja2"
            assert span.span_type == "template"
            assert span.get_tag("jinja2.template_name") == "<memory>"
            assert span.get_tag("component") == "jinja2"

        assert spans[0].name == "jinja2.compile"
        assert_is_not_measured(spans[0])
        assert spans[1].name == "jinja2.render"
        assert_is_measured(spans[1])

    def test_custom_template_name(self):
        """
        Regression test for #4008

        https://github.com/DataDog/dd-trace-py/issues/4008
        """
        # pytest.mark.parametrize is not supported for unittest test methods
        cases = [
            ("custom-name", "custom-name"),
            (1, "1"),
            ("😐", "😐"),
        ]
        for template_name, expected in cases:
            t = jinja2.environment.Template("Hello {{name}}!")
            t.name = template_name
            assert "".join(t.generate(name="Jinja")) == "Hello Jinja!"

            # tests
            spans = self.pop_spans()
            assert len(spans) == 2

            render_span = spans[1]
            assert render_span.name == "jinja2.render"
            assert render_span.get_tag("jinja2.template_name") == expected
            assert render_span.get_tag("component") == "jinja2"
            assert render_span.resource == expected

    def test_file_template(self):
        loader = jinja2.loaders.FileSystemLoader(TMPL_DIR)
        env = jinja2.Environment(loader=loader)
        t = env.get_template("template.html")
        assert t.render(name="Jinja") == "Message: Hello Jinja!"

        # tests
        spans = self.pop_spans()
        assert len(spans) == 5

        for span in spans:
            assert span.span_type == "template"
            assert span.service == "tests.contrib.jinja2"

        # templates.html extends base.html
        def get_def(s):
            return s.name, s.get_tag("jinja2.template_name")

        assert get_def(spans[0]) == ("jinja2.load", "template.html")
        assert_is_not_measured(spans[0])
        assert get_def(spans[1]) == ("jinja2.compile", "template.html")
        assert_is_not_measured(spans[1])
        assert get_def(spans[2]) == ("jinja2.render", "template.html")
        assert_is_measured(spans[2])
        assert get_def(spans[3]) == ("jinja2.load", "base.html")
        assert_is_not_measured(spans[3])
        assert get_def(spans[4]) == ("jinja2.compile", "base.html")
        assert_is_not_measured(spans[4])

        # additional checks for jinja2.load
        assert spans[0].get_tag("jinja2.template_path") == os.path.join(TMPL_DIR, "template.html")
        assert spans[3].get_tag("jinja2.template_path") == os.path.join(TMPL_DIR, "base.html")

    def test_service_name(self):
        # don't inherit the service name from the parent span, but force the value.
        loader = jinja2.loaders.FileSystemLoader(TMPL_DIR)
        env = jinja2.Environment(loader=loader)

        cfg = config._get_from(env)
        cfg["service_name"] = "renderer"

        t = env.get_template("template.html")
        assert t.render(name="Jinja") == "Message: Hello Jinja!"

        # tests
        spans = self.pop_spans()
        assert len(spans) == 5

        for span in spans:
            assert span.service == "renderer"

    def test_inherit_service(self):
        # When there is a parent span and no custom service_name, the service name is inherited
        loader = jinja2.loaders.FileSystemLoader(TMPL_DIR)
        env = jinja2.Environment(loader=loader)

        with self.tracer.trace("parent.span", service="web"):
            t = env.get_template("template.html")
            assert t.render(name="Jinja") == "Message: Hello Jinja!"

        # tests
        spans = self.pop_spans()
        assert len(spans) == 6

        for span in spans:
            assert span.service == "web"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a service name is specified by the user
            The jinja integration should use it as the service name
        """

        loader = jinja2.loaders.FileSystemLoader(TMPL_DIR)
        env = jinja2.Environment(loader=loader)
        t = env.get_template("template.html")
        assert t.render(name="Jinja") == "Message: Hello Jinja!"

        spans = self.pop_spans()
        for span in spans:
            assert span.service == "mysvc"
