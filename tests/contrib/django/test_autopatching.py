from ddtrace.monkey import patch
from .utils import DjangoTraceTestCase
from nose.tools import eq_, ok_

class DjangoAutopatchTest(DjangoTraceTestCase):
    def test_autopatching(self):
        patch(django=True)

        import django
        ok_(django._datadog_patch)
        django.setup()

        from django.conf import settings
        ok_('ddtrace.contrib.django' in settings.INSTALLED_APPS)
        eq_(settings.MIDDLEWARE_CLASSES[0], 'ddtrace.contrib.django.TraceMiddleware')


    def test_autopatching_twice(self):
        patch(django=True)

        # Call django.setup() twice and ensure we don't add a duplicate tracer
        import django
        django.setup()
        django.setup()

        from django.conf import settings
        found_app = 0

        for app in settings.INSTALLED_APPS:
            if app == 'ddtrace.contrib.django':
                found_app += 1

        eq_(found_app, 1)
        eq_(settings.MIDDLEWARE_CLASSES[0], 'ddtrace.contrib.django.TraceMiddleware')

        found_mw = 0
        for mw in settings.MIDDLEWARE_CLASSES:
            if mw == 'ddtrace.contrib.django.TraceMiddleware':
                found_mw += 1

        eq_(found_mw, 1)
