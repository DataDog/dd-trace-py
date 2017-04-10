from tornado import template

from nose.tools import eq_, ok_, assert_raises

from .utils import TornadoTestCase


class TestTornadoTemplate(TornadoTestCase):
    """
    Ensure that Tornado templates are properly traced inside and
    outside web handlers.
    """
    def test_template_handler(self):
        # it should trace the template rendering
        response = self.fetch('/template/')
        eq_(200, response.code)
        eq_('This is a rendered page called "home"\n', response.body.decode('utf-8'))

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.TemplateHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/template/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

        template_span = traces[0][1]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('templates/page.html', template_span.resource)
        eq_('templates/page.html', template_span.get_tag('tornado.template_name'))
        eq_(template_span.parent_id, request_span.span_id)
        eq_(0, template_span.error)

    def test_template_renderer(self):
        # it should trace the Template generation even outside web handlers
        t = template.Template('Hello {{ name }}!')
        value = t.generate(name='world')
        eq_(value, b'Hello world!')

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        template_span = traces[0][0]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('render_string', template_span.resource)
        eq_('render_string', template_span.get_tag('tornado.template_name'))
        eq_(0, template_span.error)

    def test_template_partials(self):
        # it should trace the template rendering when partials are used
        response = self.fetch('/template_partial/')
        eq_(200, response.code)
        eq_('This is a list:\n\n* python\n\n\n* go\n\n\n* ruby\n\n\n', response.body.decode('utf-8'))

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(5, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.TemplatePartialHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('200', request_span.get_tag('http.status_code'))
        eq_('/template_partial/', request_span.get_tag('http.url'))
        eq_(0, request_span.error)

        template_root = traces[0][1]
        eq_('tornado-web', template_root.service)
        eq_('tornado.template', template_root.name)
        eq_('template', template_root.span_type)
        eq_('templates/list.html', template_root.resource)
        eq_('templates/list.html', template_root.get_tag('tornado.template_name'))
        eq_(template_root.parent_id, request_span.span_id)
        eq_(0, template_root.error)

        template_span = traces[0][2]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('templates/item.html', template_span.resource)
        eq_('templates/item.html', template_span.get_tag('tornado.template_name'))
        eq_(template_span.parent_id, template_root.span_id)
        eq_(0, template_span.error)

        template_span = traces[0][3]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('templates/item.html', template_span.resource)
        eq_('templates/item.html', template_span.get_tag('tornado.template_name'))
        eq_(template_span.parent_id, template_root.span_id)
        eq_(0, template_span.error)

        template_span = traces[0][4]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('templates/item.html', template_span.resource)
        eq_('templates/item.html', template_span.get_tag('tornado.template_name'))
        eq_(template_span.parent_id, template_root.span_id)
        eq_(0, template_span.error)

    def test_template_exception_handler(self):
        # it should trace template rendering exceptions
        response = self.fetch('/template_exception/')
        eq_(500, response.code)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))

        request_span = traces[0][0]
        eq_('tornado-web', request_span.service)
        eq_('tornado.request', request_span.name)
        eq_('http', request_span.span_type)
        eq_('tests.contrib.tornado.web.app.TemplateExceptionHandler', request_span.resource)
        eq_('GET', request_span.get_tag('http.method'))
        eq_('500', request_span.get_tag('http.status_code'))
        eq_('/template_exception/', request_span.get_tag('http.url'))
        eq_(1, request_span.error)
        ok_('ModuleThatDoesNotExist' in request_span.get_tag('error.msg'))
        ok_('AttributeError' in request_span.get_tag('error.stack'))

        template_span = traces[0][1]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('templates/exception.html', template_span.resource)
        eq_('templates/exception.html', template_span.get_tag('tornado.template_name'))
        eq_(template_span.parent_id, request_span.span_id)
        eq_(1, template_span.error)
        ok_('ModuleThatDoesNotExist' in template_span.get_tag('error.msg'))
        ok_('AttributeError' in template_span.get_tag('error.stack'))

    def test_template_renderer_exception(self):
        # it should trace the Template exceptions generation even outside web handlers
        t = template.Template('{% module ModuleThatDoesNotExist() %}')
        with assert_raises(NameError):
            t.generate()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))

        template_span = traces[0][0]
        eq_('tornado-web', template_span.service)
        eq_('tornado.template', template_span.name)
        eq_('template', template_span.span_type)
        eq_('render_string', template_span.resource)
        eq_('render_string', template_span.get_tag('tornado.template_name'))
        eq_(1, template_span.error)
        ok_('is not defined' in template_span.get_tag('error.msg'))
        ok_('NameError' in template_span.get_tag('error.stack'))
