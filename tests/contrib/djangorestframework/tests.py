import django
from django.apps import apps
from django.test import TestCase
from nose.tools import ok_, eq_
from unittest import skipIf


@skipIf(django.VERSION < (1, 10), 'requires django version >= 1.10')
class RestFrameworkTest(TestCase):
    def setUp(self):
        # assign the default tracer
        self.tracer = settings.TRACER

        # empty the tracer spans from previous operations
        # such as database creation queries
        self.tracer.writer.spans = []
        self.tracer.writer.pop_traces()

        # would raise an exception
        from rest_framework.views import APIView
        from ddtrace.contrib.django.restframework import unpatch_restframework

        self.APIView = APIView
        self.unpatch_restframework = unpatch_restframework

    def tearDown(self):
        # empty the tracer spans from test operations
        self.tracer.writer.spans = []
        self.tracer.writer.pop_traces()

    def test_setup(self):
        ok_(apps.is_installed('rest_framework'))
        ok_(hasattr(self.APIView, '_datadog_patch'))

    def test_unpatch(self):
        self.unpatch_restframework()
        ok_(not getattr(self.APIView, '_datadog_patch'))

        response = self.client.get('/rest_framework/users/')

        # Our custom exception handler is setting the status code to 500
        eq_(response.status_code, 500)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        sp = spans[0]
        eq_(sp.name, 'django.request')
        eq_(sp.resource, 'tests.contrib.django.app.restframework.UserViewSet')
        eq_(sp.error, 0)
        eq_(sp.span_type, 'http')
        eq_(sp.get_tag('http.status_code'), '500')
        eq_(sp.get_tag('error.msg'), None)

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
