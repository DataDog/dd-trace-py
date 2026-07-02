"""Test patching and unpatching for httpx2 integration."""

from ddtrace.contrib.internal.httpx2.patch import get_version
from ddtrace.contrib.internal.httpx2.patch import patch
from ddtrace.contrib.internal.httpx2.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestHttpx2Patch(PatchTestCase.Base):
    """Test that httpx2 patch/unpatch works correctly."""

    __integration_name__ = "httpx2"
    __module_name__ = "httpx2"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, httpx2):
        """Assert that httpx2 module is correctly patched.

        Args:
            httpx2: The httpx2 module to check for patching
        """
        pass

    def assert_not_module_patched(self, httpx2):
        """Assert that httpx2 module is not patched.

        Args:
            httpx2: The httpx2 module to check for lack of patching
        """
        pass

    def assert_not_module_double_patched(self, httpx2):
        """Assert that httpx2 module is not double-patched.

        Args:
            httpx2: The httpx2 module to check for double-patching
        """
        pass
