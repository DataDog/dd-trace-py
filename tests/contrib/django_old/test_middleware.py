# 3rd party
from django.test import modify_settings
from django.db import connections

# project
from ddtrace import config
from ddtrace.ext import errors, http

# testing
from .compat import reverse
from .utils import DjangoTraceTestCase, override_ddtrace_settings


class DjangoMiddlewareTest(DjangoTraceTestCase):
    """
    Ensures that the middleware traces all Django internals
    """
    def test_middleware_trace_request(self, query_string=''):
        # ensures that the internals are properly traced
        url = reverse('users-list')
        if query_string:
            fqs = '?' + query_string
        else:
            fqs = ''
        response = self.client.get(url + fqs)
        assert response.status_code == 200

        # check for spans
        spans = self.tracer.writer.pop()
        assert len(spans) == 3
        sp_request = spans[0]
        sp_template = spans[1]
        sp_database = spans[2]
        assert sp_database.get_tag('django.db.vendor') == 'sqlite'
        assert sp_template.get_tag('django.template_name') == 'users_list.html'
        assert sp_request.get_tag('http.status_code') == '200'
        assert sp_request.get_tag(http.URL) == 'http://testserver/users/'
        assert sp_request.get_tag('django.user.is_authenticated') == 'False'
        assert sp_request.get_tag('http.method') == 'GET'
        assert sp_request.span_type == 'http'
        assert sp_request.resource == 'tests.contrib.django_old.app.views.UserList'
        if config.django.trace_query_string:
            assert sp_request.get_tag(http.QUERY_STRING) == query_string
        else:
            assert http.QUERY_STRING not in sp_request.meta

    def test_middleware_trace_request_qs(self):
        return self.test_middleware_trace_request('foo=bar')

    def test_middleware_trace_request_multi_qs(self):
        return self.test_middleware_trace_request('foo=bar&foo=baz&x=y')

    def test_middleware_trace_request_no_qs_trace(self):
        with self.override_global_config(dict(trace_query_string=True)):
            return self.test_middleware_trace_request()

    def test_middleware_trace_request_qs_trace(self):
        with self.override_global_config(dict(trace_query_string=True)):
            return self.test_middleware_trace_request('foo=bar')

    def test_middleware_trace_request_multi_qs_trace(self):
        with self.override_global_config(dict(trace_query_string=True)):
            return self.test_middleware_trace_request('foo=bar&foo=baz&x=y')

    def test_middleware_trace_request_404(self):
        """
        When making a request to an unknown url in django
            when we do not have a 404 view handler set
                we set a resource name for the default view handler
        """
        response = self.client.get('/unknown-url')
        assert response.status_code == 404

        # check for spans
        spans = self.tracer.writer.pop()
        assert len(spans) == 2
        sp_request = spans[0]
        sp_template = spans[1]

        # Template
        # DEV: The template name is `unknown` because unless they define a `404.html`
        #   django generates the template from a string, which will not have a `Template.name` set
        assert sp_template.get_tag('django.template_name') == 'unknown'

        # Request
        assert sp_request.get_tag('http.status_code') == '404'
        assert sp_request.get_tag(http.URL) == 'http://testserver/unknown-url'
        assert sp_request.get_tag('django.user.is_authenticated') == 'False'
        assert sp_request.get_tag('http.method') == 'GET'
        assert sp_request.span_type == 'http'
        assert sp_request.resource == 'django.views.defaults.page_not_found'
