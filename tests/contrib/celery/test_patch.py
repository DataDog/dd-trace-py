import unittest

from ddtrace import Pin
from tests.contrib.patch import emit_integration_and_version_to_test_agent


class CeleryPatchTest(unittest.TestCase):
    def test_patch_after_import(self):
        import celery

        from ddtrace import patch
        from ddtrace.contrib.celery import unpatch

        patch(celery=True)

        app = celery.Celery()
        assert Pin.get_from(app) is not None
        unpatch()

    def test_patch_before_import(self):
        from ddtrace import patch
        from ddtrace.contrib.celery import unpatch

        patch(celery=True)
        import celery

        app = celery.Celery()
        assert Pin.get_from(app) is not None
        unpatch()

    def test_and_emit_get_version(self):
        from ddtrace.contrib.celery import get_version

        version = get_version()
        assert type(version) == str
        assert version != ""

        emit_integration_and_version_to_test_agent("celery", version)
