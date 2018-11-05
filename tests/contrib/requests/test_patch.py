import unittest

from tests.contrib import PatchMixin


class TestRequestsPatch(PatchMixin, unittest.TestCase):
    def test_patch_before_import(self):
        from ddtrace import patch
        patch(requests=True)
        import requests # noqa
        # TODO

    def test_patch_after_import(self):
        import requests # noqa
        from ddtrace import patch
        patch(requests=True)
        # TODO

    def test_patch_idempotent(self):
        pass
