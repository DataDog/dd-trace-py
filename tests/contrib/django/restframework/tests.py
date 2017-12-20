from django.test import TestCase
from django.apps import apps
from django.core.urlresolvers import reverse

from nose.tools import ok_, eq_

from unittest import skipIf

try:
    from rest_framework.views import APIView
except Exception:
    APIView = None

from ddtrace.contrib.django.rest_framework import ORIGINAL_HANDLE_EXCEPTION, unpatch_rest_framework

from ...django.utils import DjangoTraceTestCase


@skipIf(APIView is None, 'requires rest_framework')
class RestFrameworkTest(DjangoTraceTestCase):

    def test_autopatching(self):
        ok_(apps.is_installed('rest_framework'))
        ok_(hasattr(APIView, ORIGINAL_HANDLE_EXCEPTION))

    def test_unpatch(self):
        unpatch_rest_framework()
        ok_(not hasattr(APIView, ORIGINAL_HANDLE_EXCEPTION))

    def test_tracer(self):
        ok_(self.tracer.enabled)
        eq_(self.tracer.writer.api.hostname, 'localhost')
        eq_(self.tracer.writer.api.port, 8126)
        eq_(self.tracer.tags, {'env': 'test'})
    
    def test_trace_exceptions(self):
        response = self.client.get('/users/')

        # Our custom exception handler is setting the status code to 500
        eq_(response.status_code, 500)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        sp = spans[0]
        eq_(sp.name, 'django.request')
        eq_(sp.resource, 'restframework.views.UserViewSet')
        eq_(sp.error, 1)
        eq_(sp.span_type, 'http')
        eq_(sp.get_tag('http.method'), 'GET')
        eq_(sp.get_tag('http.status_code'), '500')
        eq_(sp.get_tag('error.msg'), 'Authentication credentials were not provided.')
        ok_('NotAuthenticated' in sp.get_tag('error.stack'))
