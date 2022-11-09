from ddtrace.contrib.aiobotocore import patch
# from ddtrace.contrib.aiobotocore import unpatch
from tests.contrib.patch import PatchTestCase


class TestAiobotocorePatch(PatchTestCase.Base):
    __integration_name__ = "aiobotocore"
    __module_name__ = "aiobotocore.client"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aiobotocore_client):
        pass

    def assert_not_module_patched(self, aiobotocore_client):
        pass

    def assert_not_module_double_patched(self, aiobotocore_client):
        pass

