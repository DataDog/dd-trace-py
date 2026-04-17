from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import get_version
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAwsDurableExecutionSdkPythonPatch(PatchTestCase.Base):
    __integration_name__ = "aws_durable_execution_sdk_python"
    __module_name__ = "aws_durable_execution_sdk_python"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    # Methods that may not exist in all SDK versions — checked conditionally
    _OPTIONAL_METHODS = (
        "wait",
        "wait_for_condition",
        "wait_for_callback",
        "create_callback",
        "map",
        "parallel",
        "run_in_child_context",
    )

    def assert_module_patched(self, aws_durable_execution_sdk_python):
        from aws_durable_execution_sdk_python.context import DurableContext
        from aws_durable_execution_sdk_python.execution import durable_execution

        self.assert_wrapped(durable_execution)
        self.assert_wrapped(DurableContext.step)
        self.assert_wrapped(DurableContext.invoke)

        for method_name in self._OPTIONAL_METHODS:
            method = getattr(DurableContext, method_name, None)
            if method is not None:
                self.assert_wrapped(method)

    def assert_not_module_patched(self, aws_durable_execution_sdk_python):
        from aws_durable_execution_sdk_python.context import DurableContext
        from aws_durable_execution_sdk_python.execution import durable_execution

        self.assert_not_wrapped(durable_execution)
        self.assert_not_wrapped(DurableContext.step)
        self.assert_not_wrapped(DurableContext.invoke)

        for method_name in self._OPTIONAL_METHODS:
            method = getattr(DurableContext, method_name, None)
            if method is not None:
                self.assert_not_wrapped(method)

    def assert_not_module_double_patched(self, aws_durable_execution_sdk_python):
        from aws_durable_execution_sdk_python.context import DurableContext
        from aws_durable_execution_sdk_python.execution import durable_execution

        self.assert_not_double_wrapped(durable_execution)
        self.assert_not_double_wrapped(DurableContext.step)
        self.assert_not_double_wrapped(DurableContext.invoke)

        for method_name in self._OPTIONAL_METHODS:
            method = getattr(DurableContext, method_name, None)
            if method is not None:
                self.assert_not_double_wrapped(method)
