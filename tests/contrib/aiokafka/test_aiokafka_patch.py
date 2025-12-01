from ddtrace.contrib.internal.aiokafka.patch import get_version
from ddtrace.contrib.internal.aiokafka.patch import patch


try:
    from ddtrace.contrib.internal.aiokafka.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestAIOKafkaPatch(PatchTestCase.Base):
    __integration_name__ = "aiokafka"
    __module_name__ = "aiokafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, aiokafka):
        pass

    def assert_not_module_patched(self, aiokafka):
        pass

    def assert_not_module_double_patched(self, aiokafka):
        pass
