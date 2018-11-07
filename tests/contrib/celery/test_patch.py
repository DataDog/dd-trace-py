import unittest

from ddtrace import Pin, patch

from nose.tools import ok_
from tests.contrib import PatchMixin


class CeleryPatchTest(PatchMixin, unittest.TestCase):
    def assert_patched(self, celery):
        app = celery.Celery()
        ok_(Pin.get_from(app) is not None)

    def assert_not_patched(self, celery):
        app = celery.Celery()
        ok_(Pin.get_from(app) is None)

    def test_patch_before_import(self):
        trigger_reload = self.module_imported('celery')
        patch(celery=True)
        import celery
        if trigger_reload:
            self.reload_module(celery)
        self.assert_patched(celery)

    def test_patch_after_import(self):
        import celery
        patch(celery=True)

        self.assert_patched(celery)

    def test_patch_idempotent(self):
        pass

    def test_unpatch_before_import(self):
        from ddtrace.contrib.celery import unpatch
        patch(celery=True)
        unpatch()
        import celery
        self.assert_not_patched(celery)

    def test_unpatch_after_import(self):
        from ddtrace.contrib.celery import unpatch
        patch(celery=True)
        import celery
        unpatch()
        self.assert_not_patched(celery)
