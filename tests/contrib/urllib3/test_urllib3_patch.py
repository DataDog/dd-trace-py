from ddtrace.contrib.internal.urllib3.patch import get_version
from ddtrace.contrib.internal.urllib3.patch import patch
from ddtrace.contrib.internal.urllib3.patch import unpatch
from tests.contrib.patch import PatchTestCase


def _request_methods_class(urllib3):
    if hasattr(urllib3, "_request_methods"):
        return urllib3._request_methods.RequestMethods
    return urllib3.request.RequestMethods


class TestUrllib3Patch(PatchTestCase.Base):
    __integration_name__ = "urllib3"
    __module_name__ = "urllib3"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, urllib3):
        self.assert_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)
        self.assert_wrapped(_request_methods_class(urllib3).request)
        self.assert_wrapped(urllib3.connectionpool.HTTPConnectionPool._make_request)

    def assert_not_module_patched(self, urllib3):
        self.assert_not_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)
        self.assert_not_wrapped(_request_methods_class(urllib3).request)
        self.assert_not_wrapped(urllib3.connectionpool.HTTPConnectionPool._make_request)

    def assert_not_module_double_patched(self, urllib3):
        self.assert_not_double_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)
        self.assert_not_double_wrapped(_request_methods_class(urllib3).request)
        self.assert_not_double_wrapped(urllib3.connectionpool.HTTPConnectionPool._make_request)
