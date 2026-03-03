from ddtrace.contrib.internal.mlflow.patch import get_version
from ddtrace.contrib.internal.mlflow.patch import patch
from ddtrace.contrib.internal.mlflow.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestMLflowPatch(PatchTestCase.Base):
    __integration_name__ = "mlflow"
    __module_name__ = "mlflow"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, mlflow):
        pass

    def assert_not_module_patched(self, mlflow):
        pass

    def assert_not_module_double_patched(self, mlflow):
        pass
