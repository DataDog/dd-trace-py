from unittest import mock

import celery
import pytest

from ddtrace import config
from ddtrace.contrib.celery import patch_app
from ddtrace.contrib.internal.celery.app import _traced_beat_function
from ddtrace.contrib.internal.celery.patch import unpatch_app

from .base import CeleryBaseTestCase


class CeleryAppTest(CeleryBaseTestCase):
    """Ensures the default application is properly instrumented"""

    def test_patch_app(self):
        assert getattr(celery.Celery(), "__datadog_patch", False)

    def test_patch_app_legacy_argument(self):
        unpatch_app(celery.Celery)
        patch_app(celery.Celery, pin=None)
        assert getattr(celery.Celery(), "__datadog_patch", False)

    def test_unpatch_app(self):
        unpatch_app(celery.Celery)
        assert not getattr(celery.Celery(), "__datadog_patch", False)


@pytest.mark.parametrize("worker_service", ["celery-worker", "custom-worker"])
def test_beat_uses_worker_service(worker_service, test_spans):
    with mock.patch.dict(config.celery, worker_service_name=worker_service, service="other-service"):
        wrapper = _traced_beat_function(config.celery, "tick")
        assert wrapper(lambda: 5, None, (), {}) == 5
    spans = test_spans.get_spans()
    assert len(spans) == 1
    assert spans[0].service == worker_service
