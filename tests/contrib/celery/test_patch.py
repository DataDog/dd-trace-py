import unittest
from nose.tools import ok_
from ddtrace import Pin


class CeleryPatchTest(unittest.TestCase):
    def test_patch_after_import(self):
        import celery
        from ddtrace import patch
        patch(celery=True)

        app = celery.Celery()
        ok_(Pin.get_from(app) is not None)

    def test_patch_before_import(self):
        from ddtrace import patch
        patch(celery=True)
        import celery

        app = celery.Celery()
        ok_(Pin.get_from(app) is not None)
