import unittest

from ddtrace import patch

from tests.contrib import PatchMixin


class TestRequestsPatch(PatchMixin, unittest.TestCase):
    def assert_patched(self, requests):
        self.assert_wrapped(requests.Session.request)

    def assert_not_patched(self, requests):
        self.assert_not_wrapped(requests.Session.request)

    def test_patch_before_import(self):
        patch(requests=True)
        import requests
        self.assert_patched(requests)

    def test_patch_after_import(self):
        import requests
        patch(requests=True)
        self.assert_patched(requests)

    def test_patch_idempotent(self):
        patch(requests=True)
        patch(requests=True)
        import requests
        self.assert_not_double_wrapped(requests.Session.request)

    def test_unpatch_before_import(self):
        from ddtrace.contrib.requests import unpatch
        patch(requests=True)
        unpatch()
        import requests
        self.assert_not_patched(requests)

    def test_unpatch_after_import(self):
        from ddtrace.contrib.requests import unpatch
        patch(requests=True)
        import requests
        unpatch()
        self.assert_not_patched(requests)
