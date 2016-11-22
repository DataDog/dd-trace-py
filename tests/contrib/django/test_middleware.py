# 3rd party
from nose.tools import eq_

from django.test import modify_settings
from django.core.urlresolvers import reverse

# project
from ddtrace.contrib.django.conf import settings
from ddtrace.contrib.django import TraceMiddleware

# testing
from .utils import DjangoTraceTestCase


class DjangoMiddlewareTest(DjangoTraceTestCase):
    """
    Ensures that the middleware traces all Django internals
    """
    def test_middleware_trace_request(self):
        # ensures that the internals are properly traced
        url = reverse('users-list')
        response = self.client.get(url)
        eq_(response.status_code, 200)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)
        sp_database = spans[0]
        sp_template = spans[1]
        sp_request = spans[2]
        eq_(sp_database.get_tag('django.db.vendor'), 'sqlite')
        eq_(sp_template.get_tag('django.template_name'), 'users_list.html')
        eq_(sp_request.get_tag('http.status_code'), '200')
        eq_(sp_request.get_tag('http.url'), '/users/')
        eq_(sp_request.get_tag('django.user.is_authenticated'), 'False')
        eq_(sp_request.get_tag('http.method'), 'GET')

    def test_middleware_trace_errors(self):
        # ensures that the internals are properly traced
        url = reverse('forbidden-view')
        response = self.client.get(url)
        eq_(response.status_code, 403)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag('http.status_code'), '403')
        eq_(span.get_tag('http.url'), '/fail-view/')

    @modify_settings(
        MIDDLEWARE={
            'remove': 'django.contrib.auth.middleware.AuthenticationMiddleware',
        },
        MIDDLEWARE_CLASSES={
            'remove': 'django.contrib.auth.middleware.AuthenticationMiddleware',
        },
    )
    def test_middleware_without_user(self):
        # remove the AuthenticationMiddleware so that the ``request``
        # object doesn't have the ``user`` field
        url = reverse('users-list')
        response = self.client.get(url)
        eq_(response.status_code, 200)

        # check for spans
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)
        sp_database = spans[0]
        sp_template = spans[1]
        sp_request = spans[2]
        eq_(sp_request.get_tag('http.status_code'), '200')
        eq_(sp_request.get_tag('django.user.is_authenticated'), None)
