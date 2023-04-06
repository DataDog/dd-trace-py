from ddtrace.contrib.httplib.patch import patch
from ddtrace.internal.compat import PY2


try:
    from ddtrace.contrib.httplib.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestHttplibPatch(PatchTestCase.Base):
    __integration_name__ = "httplib"
    __module_name__ = "httplib" if PY2 else "http.client"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, http_client):
        pass

    def assert_not_module_patched(self, http_client):
        pass

    def assert_not_module_double_patched(self, http_client):
        pass
