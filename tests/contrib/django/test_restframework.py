import django
from django.apps import apps
from nose.tools import ok_, eq_
from unittest import skipIf

from .utils import DjangoTraceTestCase

@skipIf(django.VERSION < (1, 10), 'requires django version >= 1.10')
class RestFrameworkTest(DjangoTraceTestCase):
    def setUp(self):
        super(RestFrameworkTest, self).setUp()

        # We do the imports here because importing rest_framework with an older version of Django 
        # would raise an exception
        from rest_framework.views import APIView
        from ddtrace.contrib.django.restframework import ORIGINAL_HANDLE_EXCEPTION, unpatch_rest_framework

        self.APIView = APIView
        self.ORIGINAL_HANDLE_EXCEPTION = ORIGINAL_HANDLE_EXCEPTION
        self.unpatch_rest_framework = unpatch_rest_framework

    def test_setup(self):
        ok_(apps.is_installed('rest_framework'))
        ok_(hasattr(self.APIView, self.ORIGINAL_HANDLE_EXCEPTION))

    def test_unpatch(self):
        self.unpatch_rest_framework()
        ok_(not hasattr(self.APIView, self.ORIGINAL_HANDLE_EXCEPTION))

    def test_tracer(self):
        ok_(self.tracer.enabled)
        eq_(self.tracer.writer.api.hostname, 'localhost')
        eq_(self.tracer.writer.api.port, 8126)
        eq_(self.tracer.tags, {'env': 'test'})
    
    def test_trace_exceptions(self):
        response = self.client.get('/rest_framework/users/')

        # Our custom exception handler is setting the status code to 500
        eq_(response.status_code, 500)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        sp = spans[0]
        eq_(sp.name, 'django.request')
        eq_(sp.resource, 'tests.contrib.django.app.restframework.UserViewSet')
        eq_(sp.error, 1)
        eq_(sp.span_type, 'http')
        eq_(sp.get_tag('http.method'), 'GET')
        eq_(sp.get_tag('http.status_code'), '500')
        eq_(sp.get_tag('error.msg'), 'Authentication credentials were not provided.')
        ok_('NotAuthenticated' in sp.get_tag('error.stack'))
