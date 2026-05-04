from aws_durable_execution_sdk_python import execution
from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.operation.base import OperationExecutor
from aws_durable_execution_sdk_python.operation.step import StepOperationExecutor

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

    def assert_module_patched(self, aws_durable_execution_sdk_python):
        self.assert_wrapped(aws_durable_execution_sdk_python.durable_execution)
        self.assert_wrapped(execution.durable_execution)
        self.assert_wrapped(DurableContext.invoke)
        self.assert_wrapped(DurableContext.step)
        self.assert_wrapped(DurableContext.wait)
        self.assert_wrapped(DurableContext.wait_for_condition)
        self.assert_wrapped(DurableContext.wait_for_callback)
        self.assert_wrapped(DurableContext.create_callback)
        self.assert_wrapped(DurableContext.map)
        self.assert_wrapped(DurableContext.parallel)
        self.assert_wrapped(DurableContext.run_in_child_context)
        self.assert_wrapped(OperationExecutor.process)
        self.assert_wrapped(StepOperationExecutor.retry_handler)

    def assert_not_module_patched(self, aws_durable_execution_sdk_python):
        self.assert_not_wrapped(aws_durable_execution_sdk_python.durable_execution)
        self.assert_not_wrapped(execution.durable_execution)
        self.assert_not_wrapped(DurableContext.invoke)
        self.assert_not_wrapped(DurableContext.step)
        self.assert_not_wrapped(DurableContext.wait)
        self.assert_not_wrapped(DurableContext.wait_for_condition)
        self.assert_not_wrapped(DurableContext.wait_for_callback)
        self.assert_not_wrapped(DurableContext.create_callback)
        self.assert_not_wrapped(DurableContext.map)
        self.assert_not_wrapped(DurableContext.parallel)
        self.assert_not_wrapped(DurableContext.run_in_child_context)
        self.assert_not_wrapped(OperationExecutor.process)
        self.assert_not_wrapped(StepOperationExecutor.retry_handler)

    def assert_not_module_double_patched(self, aws_durable_execution_sdk_python):
        self.assert_not_double_wrapped(aws_durable_execution_sdk_python.durable_execution)
        self.assert_not_double_wrapped(execution.durable_execution)
        self.assert_not_double_wrapped(DurableContext.invoke)
        self.assert_not_double_wrapped(DurableContext.step)
        self.assert_not_double_wrapped(DurableContext.wait)
        self.assert_not_double_wrapped(DurableContext.wait_for_condition)
        self.assert_not_double_wrapped(DurableContext.wait_for_callback)
        self.assert_not_double_wrapped(DurableContext.create_callback)
        self.assert_not_double_wrapped(DurableContext.map)
        self.assert_not_double_wrapped(DurableContext.parallel)
        self.assert_not_double_wrapped(DurableContext.run_in_child_context)
        self.assert_not_double_wrapped(OperationExecutor.process)
        self.assert_not_double_wrapped(StepOperationExecutor.retry_handler)
