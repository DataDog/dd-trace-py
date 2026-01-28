import asyncio

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
import ddtrace.contrib  # noqa: F401
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_ACTIVITY_TRIGGER
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_ENTITY_TRIGGER
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_ORCHESTRATION_TRIGGER
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_TRIGGER_DEFS
from ddtrace.contrib.internal.azure_durable_functions.patch import _patched_get_functions
from ddtrace.contrib.internal.azure_durable_functions.patch import _wrap_durable_trigger
from ddtrace.contrib.internal.azure_durable_functions.patch import patch as durable_patch
from ddtrace.contrib.internal.azure_durable_functions.patch import unpatch as durable_unpatch
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_faas_operation
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


class _StubTrigger:
    def __init__(self, binding_name):
        self._binding_name = binding_name

    def get_binding_name(self):
        return self._binding_name


class _StubFunction:
    def __init__(self, name, trigger, func):
        self._name = name
        self._trigger = trigger
        self._func = func

    def get_trigger(self):
        return self._trigger

    def get_function_name(self):
        return self._name

    def get_user_function(self):
        return self._func


def _make_pin(tracer):
    pin = Pin()
    pin._tracer = tracer
    return pin


def test_activity_trigger_wrapper_sync():
    with scoped_tracer() as tracer:
        pin = _make_pin(tracer)

        def activity(name):
            return f"activity:{name}"

        trigger_name, context_name = _DURABLE_TRIGGER_DEFS[_DURABLE_ACTIVITY_TRIGGER]
        wrapped = _wrap_durable_trigger(pin, activity, "sample_activity", trigger_name, context_name)

        assert wrapped("test") == "activity:test"

        spans = TracerSpanContainer(tracer).pop()
        assert len(spans) == 1
        span = spans[0]

        expected_name = schematize_cloud_faas_operation(
            "azure.durable_functions.invoke", cloud_provider="azure", cloud_service="functions"
        )
        assert span.name == expected_name
        assert span.service == int_service(pin, config.azure_durable_functions)
        assert span.resource == "Activity sample_activity"
        assert span.span_type == SpanTypes.SERVERLESS
        assert span.get_tag(COMPONENT) == "azure_durable_functions"
        assert span.get_tag("aas.function.name") == "sample_activity"
        assert span.get_tag("aas.function.trigger") == "Activity"
        assert span.get_tag(SPAN_KIND) == SpanKind.INTERNAL


def test_entity_trigger_wrapper_async():
    with scoped_tracer() as tracer:
        pin = _make_pin(tracer)

        async def entity():
            return "ok"

        trigger_name, context_name = _DURABLE_TRIGGER_DEFS[_DURABLE_ENTITY_TRIGGER]
        wrapped = _wrap_durable_trigger(pin, entity, "sample_entity", trigger_name, context_name)

        assert asyncio.run(wrapped()) == "ok"

        spans = TracerSpanContainer(tracer).pop()
        assert len(spans) == 1
        span = spans[0]

        assert span.resource == "Entity sample_entity"
        assert span.get_tag("aas.function.trigger") == "Entity"
        assert span.get_tag(SPAN_KIND) == SpanKind.INTERNAL


def test_patched_get_functions_wraps_activity_and_entity_only():
    with scoped_tracer() as tracer:
        pin = _make_pin(tracer)

        class _StubInstance:
            pass

        instance = _StubInstance()
        pin.onto(instance)

        def user_func():
            return "ok"

        activity_fn = _StubFunction("activity", _StubTrigger(_DURABLE_ACTIVITY_TRIGGER), user_func)
        entity_fn = _StubFunction("entity", _StubTrigger(_DURABLE_ENTITY_TRIGGER), user_func)
        orchestration_fn = _StubFunction("orchestrator", _StubTrigger(_DURABLE_ORCHESTRATION_TRIGGER), user_func)
        no_trigger_fn = _StubFunction("no_trigger", None, user_func)

        def wrapped():
            return [activity_fn, entity_fn, orchestration_fn, no_trigger_fn]

        durable_patch()
        try:
            functions = _patched_get_functions(wrapped, instance, (), {})
        finally:
            durable_unpatch()

        assert functions[0]._func is not user_func
        assert functions[1]._func is not user_func
        assert functions[2]._func is user_func
        assert functions[3]._func is user_func
