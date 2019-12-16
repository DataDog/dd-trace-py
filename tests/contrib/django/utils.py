from ddtrace import Pin
from ddtrace.contrib.django import patch
import django
from django.test import Client

from tests.base import BaseTracerTestCase
from tests.subprocesstest import SubprocessTestCase
from ...utils.span import TracerSpanContainer


class DjangoTestCase(BaseTracerTestCase, SubprocessTestCase):
    """
    Base ddtrace Django test case.

    Provides `self.client` for querying the specified Django app.

    Individual test cases can be run in subprocess interpreters by decorating
    them with @run_in_subprocess.

    Usage::

        from .compat import reverse
        from .utils import DjangoTestCase
        class MyDjangoTestCase(DjangoTestCase):
            APP_NAME = 'myapp'

            def test_something(self):
                resp = self.client.get(reverse('some-endpoint'))
                # ...
    """

    APP_NAME = None

    def setUp(self):
        super(DjangoTestCase, self).setUp()

        if self.APP_NAME is None:
            raise NotImplementedError('An APP_NAME must be specified on your test class.')

        settings_file = 'tests.contrib.django.{}.settings'.format(self.APP_NAME)

        # Patch BEFORE performing django.setup()
        # The current patching system has to be called before django.setup()
        patch()

        with self.override_env(dict(DJANGO_SETTINGS_MODULE=settings_file)):
            django.setup()
            self.client = Client()

        Pin.override(django, tracer=self.tracer)
        self.test_spans = TracerSpanContainer(self.tracer)

    def tearDown(self):
        self.test_spans.reset()
        super(DjangoTestCase, self).tearDown()
