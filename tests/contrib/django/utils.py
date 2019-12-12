from ddtrace import Pin
from ddtrace.contrib.django import patch, unpatch
import django
from django.conf import settings
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

        # DEV: due to a pytest-django fixture `_dj_autoclear_mailbox`
        # calling del mail.outbox[:] before mail.outbox is set we need to
        # create it ahead of time. Not quite sure why this happens yet.
        django.core.mail.outbox = []

        if self.APP_NAME is None:
            raise NotImplementedError('An APP_NAME must be specified on your test class.')

        settings_file = 'tests.contrib.django.{}.settings'.format(self.APP_NAME)

        with self.override_env(dict(DJANGO_SETTINGS_MODULE=settings_file)):
            if django.VERSION < (1, 7, 0):
                settings.configure()
            else:
                django.setup()

            self.client = Client()

        patch()
        Pin.override(django, tracer=self.tracer)
        self.test_spans = TracerSpanContainer(self.tracer)

    def tearDown(self):
        super(DjangoTestCase, self).tearDown()
        self.test_spans.reset()
