from ddtrace.contrib.internal.http_server.patch import get_version
from ddtrace.contrib.internal.http_server.patch import patch
from ddtrace.contrib.internal.http_server.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestHttpServerPatch(PatchTestCase.Base):
    __integration_name__ = "http_server"
    __module_name__ = "http.server"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, http_server):
        self.assert_wrapped(http_server.BaseHTTPRequestHandler.parse_request)

    def assert_not_module_patched(self, http_server):
        self.assert_not_wrapped(http_server.BaseHTTPRequestHandler.parse_request)

    def assert_not_module_double_patched(self, http_server):
        self.assert_not_double_wrapped(http_server.BaseHTTPRequestHandler.parse_request)

    def test_and_emit_get_version(self):
        version = get_version()
        assert isinstance(version, str)
        assert version == ""
