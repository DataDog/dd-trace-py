import django

from ddtrace.monkey import patch
from .utils import DjangoTraceTestCase
from nose.tools import eq_, ok_
from django.conf import settings
from unittest import skipIf


class DjangoAutopatchTest(DjangoTraceTestCase):
    def setUp(self):
        super(DjangoAutopatchTest, self).setUp()
        patch(django=True)
        django.setup()

    @skipIf(django.VERSION >= (1, 10), 'skip if version above 1.10')
    def test_autopatching_middleware_classes(self):
        ok_(django._datadog_patch)
        ok_('ddtrace.contrib.django' in settings.INSTALLED_APPS)
        eq_(settings.MIDDLEWARE_CLASSES[0], 'ddtrace.contrib.django.TraceMiddleware')
        eq_(settings.MIDDLEWARE_CLASSES[-1], 'ddtrace.contrib.django.TraceExceptionMiddleware')


    @skipIf(django.VERSION >= (1, 10), 'skip if version above 1.10')
    def test_autopatching_twice_middleware_classes(self):
        ok_(django._datadog_patch)
        # Call django.setup() twice and ensure we don't add a duplicate tracer
        django.setup()

        found_app = settings.INSTALLED_APPS.count('ddtrace.contrib.django')
        eq_(found_app, 1)

        eq_(settings.MIDDLEWARE_CLASSES[0], 'ddtrace.contrib.django.TraceMiddleware')
        eq_(settings.MIDDLEWARE_CLASSES[-1], 'ddtrace.contrib.django.TraceExceptionMiddleware')

        found_mw = settings.MIDDLEWARE_CLASSES.count('ddtrace.contrib.django.TraceMiddleware')
        eq_(found_mw, 1)
        found_mw = settings.MIDDLEWARE_CLASSES.count('ddtrace.contrib.django.TraceExceptionMiddleware')
        eq_(found_mw, 1)

    @skipIf(django.VERSION < (1, 10), 'skip if version is below 1.10')
    def test_autopatching_middleware(self):
        ok_(django._datadog_patch)
        ok_('ddtrace.contrib.django' in settings.INSTALLED_APPS)
        eq_(settings.MIDDLEWARE[0], 'ddtrace.contrib.django.TraceMiddleware')
        # MIDDLEWARE_CLASSES gets created internally in django 1.10 & 1.11 but doesn't
        # exist at all in 2.0.
        ok_(not getattr(settings, 'MIDDLEWARE_CLASSES', None) or
            'ddtrace.contrib.django.TraceMiddleware' not in settings.MIDDLEWARE_CLASSES)
        eq_(settings.MIDDLEWARE[-1], 'ddtrace.contrib.django.TraceExceptionMiddleware')
        ok_(not getattr(settings, 'MIDDLEWARE_CLASSES', None) or
            'ddtrace.contrib.django.TraceExceptionMiddleware' not in settings.MIDDLEWARE_CLASSES)


    @skipIf(django.VERSION < (1, 10), 'skip if version is below 1.10')
    def test_autopatching_twice_middleware(self):
        ok_(django._datadog_patch)
        # Call django.setup() twice and ensure we don't add a duplicate tracer
        django.setup()

        found_app = settings.INSTALLED_APPS.count('ddtrace.contrib.django')
        eq_(found_app, 1)

        eq_(settings.MIDDLEWARE[0], 'ddtrace.contrib.django.TraceMiddleware')
        # MIDDLEWARE_CLASSES gets created internally in django 1.10 & 1.11 but doesn't
        # exist at all in 2.0.
        ok_(not getattr(settings, 'MIDDLEWARE_CLASSES', None) or
            'ddtrace.contrib.django.TraceMiddleware' not in settings.MIDDLEWARE_CLASSES)
        eq_(settings.MIDDLEWARE[-1], 'ddtrace.contrib.django.TraceExceptionMiddleware')
        ok_(not getattr(settings, 'MIDDLEWARE_CLASSES', None) or
            'ddtrace.contrib.django.TraceExceptionMiddleware' not in settings.MIDDLEWARE_CLASSES)

        found_mw = settings.MIDDLEWARE.count('ddtrace.contrib.django.TraceMiddleware')
        eq_(found_mw, 1)

        found_mw = settings.MIDDLEWARE.count('ddtrace.contrib.django.TraceExceptionMiddleware')
        eq_(found_mw, 1)
