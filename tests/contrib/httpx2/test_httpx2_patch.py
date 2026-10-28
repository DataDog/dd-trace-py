from ddtrace.contrib.internal.httpx2.patch import get_version
from ddtrace.contrib.internal.httpx2.patch import patch
from ddtrace.contrib.internal.httpx2.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestHttpx2Patch(PatchTestCase.Base):
    __integration_name__ = "httpx2"
    __module_name__ = "httpx2"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, httpx2):
        self.assert_wrapped(httpx2.Client.send)
        self.assert_wrapped(httpx2.AsyncClient.send)
        self.assert_wrapped(httpx2.Client._send_single_request)
        self.assert_wrapped(httpx2.AsyncClient._send_single_request)

    def assert_not_module_patched(self, httpx2):
        self.assert_not_wrapped(httpx2.Client.send)
        self.assert_not_wrapped(httpx2.AsyncClient.send)
        self.assert_not_wrapped(httpx2.Client._send_single_request)
        self.assert_not_wrapped(httpx2.AsyncClient._send_single_request)

    def assert_not_module_double_patched(self, httpx2):
        self.assert_not_double_wrapped(httpx2.Client.send)
        self.assert_not_double_wrapped(httpx2.AsyncClient.send)
        self.assert_not_double_wrapped(httpx2.Client._send_single_request)
        self.assert_not_double_wrapped(httpx2.AsyncClient._send_single_request)
