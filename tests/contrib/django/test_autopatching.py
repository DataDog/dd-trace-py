from ddtrace.monkey import patch
from .utils import DjangoTraceTestCase
from nose.tools import eq_, ok_

class DjangoAutopatchTest(DjangoTraceTestCase):
    def test_autopatching(self):
        patch(django=True)

        import django
        ok_(django._datadog_patch)

        from django.conf import settings
        ok_('ddtrace.contrib.django' in settings.INSTALLED_APPS)
        eq_(settings.MIDDLEWARE_CLASSES[0], 'ddtrace.contrib.django.TraceMiddleware')
