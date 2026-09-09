import sys
from types import SimpleNamespace

import azure.durable_functions as durable_functions

from ddtrace import config
from ddtrace.contrib.internal.azure_durable_functions.patch import get_version
from ddtrace.contrib.internal.azure_durable_functions.patch import patch
from ddtrace.contrib.internal.azure_durable_functions.patch import unpatch
from ddtrace.contrib.internal.azure_functions._worker import _run_sync_with_context
from ddtrace.contrib.internal.azure_functions.shared import _get_azure_invocation_context
from ddtrace.contrib.internal.azure_functions.shared import _get_orchestration_parent_context
from tests.contrib.patch import PatchTestCase


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
MISSING = object()


class TestAzureDurableFunctionsPatch(PatchTestCase.Base):
    __integration_name__ = "azure_durable_functions"
    __module_name__ = "azure.durable_functions"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @staticmethod
    def _get_dfapp():
        from azure.durable_functions.decorators import durable_app

        return durable_app.DFApp

    @staticmethod
    def _get_client():
        from azure.durable_functions.models.DurableOrchestrationClient import DurableOrchestrationClient

        return DurableOrchestrationClient

    def assert_module_patched(self, durable_functions):
        self.assert_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_wrapped(self._get_client()._get_current_activity_context)

    def assert_not_module_patched(self, durable_functions):
        self.assert_not_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_not_wrapped(self._get_client()._get_current_activity_context)

    def assert_not_module_double_patched(self, durable_functions):
        self.assert_not_double_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_not_double_wrapped(self._get_client()._get_current_activity_context)


def test_patch_import_failure_does_not_mark_module_patched(monkeypatch):
    unpatch()
    monkeypatch.setitem(sys.modules, "azure.durable_functions.decorators", None)

    patch()

    assert not getattr(durable_functions, "_datadog_patch", False)


def test_distributed_tracing_disabled_ignores_durable_parent_context():
    invocation_context = SimpleNamespace(trace_context=SimpleNamespace(trace_parent=TRACEPARENT, trace_state="dd=s:1"))
    orchestration_data = {
        "history": [
            {
                "EventType": 0,
                "ParentTraceContext": {"TraceParent": TRACEPARENT, "TraceState": "dd=s:1"},
            }
        ]
    }

    def assert_no_parent_context(*_):
        distributed_tracing = config.azure_functions.get("distributed_tracing", MISSING)
        config.azure_functions["distributed_tracing"] = False
        try:
            assert _get_azure_invocation_context() is None
            assert _get_orchestration_parent_context((orchestration_data,), {}, "context") is None
        finally:
            if distributed_tracing is MISSING:
                del config.azure_functions["distributed_tracing"]
            else:
                config.azure_functions["distributed_tracing"] = distributed_tracing

    _run_sync_with_context(assert_no_parent_context, None, ("invocation-id", invocation_context), {})
