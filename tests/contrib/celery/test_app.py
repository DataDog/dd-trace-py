import celery

from nose.tools import ok_

from ddtrace import Pin
from ddtrace.contrib.celery import unpatch_app

from .base import CeleryBaseTestCase


class CeleryAppTest(CeleryBaseTestCase):
    """Ensures the default application is properly instrumented"""

    def test_patch_app(self):
        # When celery.App is patched it must include a `Pin` instance
        app = celery.Celery()
        ok_(Pin.get_from(app) is not None)

    def test_unpatch_app(self):
        # When celery.App is unpatched it must not include a `Pin` instance
        unpatch_app(celery.Celery)
        app = celery.Celery()
        ok_(Pin.get_from(app) is None)
