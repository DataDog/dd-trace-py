from ddtrace import Pin
from ddtrace.contrib.django import patch, unpatch

import tests
from tests.contrib.patch import PatchMixin
from .compat import reverse
from .utils import DjangoTestCase


import django


class TestDjangoApp(DjangoTestCase, PatchMixin):
    APP_NAME = 'django_app'

    def setUp(self):
        patch()
        super(TestDjangoApp, self).setUp()
        Pin.override(django, tracer=self.tracer)

    def tearDown(self):
        super(TestDjangoApp, self).tearDown()
        unpatch()

    def test_patched(self):
        """
        When django is patched
            - Patching entry-points should be installed
            - Middlewares should be patched
        """
        self.assert_wrapped(django.apps.registry.Apps.populate)

    def test_middleware_patching(self):
        # Test that the entry point is installed
        self.assert_wrapped(django.core.handlers.base.BaseHandler.load_middleware)

        # Test that various middlewares that are included are patched
        self.assert_wrapped(django.middleware.common.CommonMiddleware.process_request)
        self.assert_wrapped(django.middleware.security.SecurityMiddleware.process_request)

        # Test that each middleware hook is patched
        self.assert_wrapped(tests.contrib.django.django_app.middleware.EverythingMiddleware.process_response)
        self.assert_wrapped(tests.contrib.django.django_app.middleware.EverythingMiddleware.process_request)
        self.assert_wrapped(tests.contrib.django.django_app.middleware.EverythingMiddleware.process_view)
        self.assert_wrapped(tests.contrib.django.django_app.middleware.EverythingMiddleware.process_template_response)
        self.assert_wrapped(tests.contrib.django.django_app.middleware.EverythingMiddleware.process_exception)
        self.assert_wrapped(tests.contrib.django.django_app.middleware.fn_middleware(None))

    def test_middleware(self):
        url = reverse('fn-view')
        response = self.client.get(url)
        spans = self.get_spans()
        assert response.status_code == 200
        assert len(spans) > 0
