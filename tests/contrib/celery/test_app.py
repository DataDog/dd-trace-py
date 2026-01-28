import celery

from ddtrace.contrib.internal.celery.patch import unpatch_app

from .base import CeleryBaseTestCase


class CeleryAppTest(CeleryBaseTestCase):
    """Ensures the default application is properly instrumented"""

    def test_patch_app(self):
        # When celery.App is patched it must include a `Pin` instance
        assert getattr(celery.Celery(), "__datadog_patch", False)

    def test_unpatch_app(self):
        # When celery.App is unpatched it must not include a `Pin` instance
        unpatch_app(celery.Celery)
        assert not getattr(celery.Celery(), "__datadog_patch", False)
