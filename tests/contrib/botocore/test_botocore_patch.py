from ddtrace.contrib.botocore import patch
# from ddtrace.contrib.botocore import unpatch
from tests.contrib.patch import PatchTestCase


class TestBotocorePatch(PatchTestCase.Base):
    __integration_name__ = "botocore"
    __module_name__ = "botocore.client"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, botocore_client):
        pass

    def assert_not_module_patched(self, botocore_client):
        pass

    def assert_not_module_double_patched(self, botocore_client):
        pass

