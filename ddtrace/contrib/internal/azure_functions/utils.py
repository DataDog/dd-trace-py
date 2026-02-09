import functools
import inspect
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import Optional
from typing import Union

import azure.functions as azure_functions

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import azure_eventhubs as azure_eventhubsx
from ddtrace.ext import azure_servicebus as azure_servicebusx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.propagation.http import HTTPPropagator


def create_context(
    context_name: str, pin: Pin, resource: Optional[str] = None, headers: Optional[dict] = None
) -> core.ExecutionContext:
    operation_name = schematize_cloud_faas_operation(
        "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
    )
    return core.context_with_data(
        context_name,
        span_name=operation_name,
        pin=pin,
        resource=resource,
        service=int_service(pin, config.azure_functions),
        span_type=SpanTypes.SERVERLESS,
        distributed_headers=headers,
        integration_config=config.azure_functions,
        activate_distributed_headers=True,
    )


def wrap_function_with_tracing(
    func: Callable[..., Any],
    context_factory: Callable[[Any], core.ExecutionContext],
    pre_dispatch: Optional[Callable[[core.ExecutionContext, Any], tuple[str, tuple[Any, ...]]]] = None,
    post_dispatch: Optional[Callable[[core.ExecutionContext, Any], tuple[str, tuple[Any, ...]]]] = None,
) -> Union[Callable[..., Any], Callable[..., Coroutine[Any, Any, Any]]]:
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with context_factory(kwargs) as ctx:
                if pre_dispatch:
                    core.dispatch(*pre_dispatch(ctx, kwargs))

                res = None
                try:
                    res = await func(*args, **kwargs)
                    return res
                finally:
                    if post_dispatch:
                        core.dispatch(*post_dispatch(ctx, res))

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with context_factory(kwargs) as ctx:
            if pre_dispatch:
                core.dispatch(*pre_dispatch(ctx, kwargs))

            res = None
            try:
                res = func(*args, **kwargs)
                return res
            finally:
                if post_dispatch:
                    core.dispatch(*post_dispatch(ctx, res))

    return wrapper


def wrap_durable_trigger(pin, func, function_name, trigger_type, context_name):
    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context(context_name, pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        return (
            "azure.durable_functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger_type, SpanKind.INTERNAL),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def wrap_http_trigger(pin, func, function_name, trigger_arg_name):
    trigger_type = "Http"

    def context_factory(kwargs):
        req = kwargs.get(trigger_arg_name)
        return create_context("azure.functions.patched_route_request", pin, headers=req.headers)

    def pre_dispatch(ctx, kwargs):
        req = kwargs.get(trigger_arg_name)
        return ("azure.functions.request_call_modifier", (ctx, config.azure_functions, req))

    def post_dispatch(ctx, res):
        return ("azure.functions.start_response", (ctx, config.azure_functions, res, function_name, trigger_type))

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch, post_dispatch=post_dispatch)


def wrap_timer_trigger(pin, func, function_name):
    trigger_type = "Timer"

    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context("azure.functions.patched_timer", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        return (
            "azure.functions.trigger_call_modifier",
            (ctx, config.azure_functions, function_name, trigger_type, SpanKind.INTERNAL),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def wrap_service_bus_trigger(pin, func, function_name, trigger_arg_name, trigger_details):
    trigger_type = "ServiceBus"

    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context("azure.functions.patched_service_bus", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        entity_name = trigger_details.get("topicName") or trigger_details.get("queueName")
        cardinality = trigger_details.get("cardinality")
        msg_arg_value = kwargs.get(trigger_arg_name)

        if (
            cardinality == azure_functions.Cardinality.MANY
            and isinstance(msg_arg_value, list)
            and isinstance(msg_arg_value[0], azure_functions.ServiceBusMessage)
        ):
            batch_count = str(len(msg_arg_value))
            fully_qualified_namespace = (
                getattr(msg_arg_value[0], "metadata", {}).get("Client", {}).get("FullyQualifiedNamespace")
            )
            message_id = None

            if config.azure_functions.distributed_tracing:
                for message in msg_arg_value:
                    parent_context = HTTPPropagator.extract(message.application_properties)
                    if parent_context.trace_id is not None and parent_context.span_id is not None:
                        ctx.span.link_span(parent_context)
        elif isinstance(msg_arg_value, azure_functions.ServiceBusMessage):
            batch_count = None
            fully_qualified_namespace = (
                getattr(msg_arg_value, "metadata", {}).get("Client", {}).get("FullyQualifiedNamespace")
            )
            message_id = msg_arg_value.message_id

            if config.azure_functions.distributed_tracing:
                parent_context = HTTPPropagator.extract(msg_arg_value.application_properties)
                if parent_context.trace_id is not None and parent_context.span_id is not None:
                    ctx.span.link_span(parent_context)
        else:
            batch_count = None
            fully_qualified_namespace = None
            message_id = None

        return (
            "azure.functions.service_bus_trigger_modifier",
            (
                ctx,
                config.azure_functions,
                function_name,
                trigger_type,
                SpanKind.CONSUMER,
                azure_servicebusx.RECEIVE,
                azure_servicebusx.SERVICE,
                entity_name,
                fully_qualified_namespace,
                message_id,
                batch_count,
            ),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def wrap_event_hubs_trigger(pin, func, function_name, trigger_arg_name, trigger_details):
    trigger_type = "EventHubs"

    def context_factory(kwargs):
        resource_name = f"{trigger_type} {function_name}"
        return create_context("azure.functions.patched_event_hubs", pin, resource_name)

    def pre_dispatch(ctx, kwargs):
        entity_name = trigger_details.get("eventHubName")
        cardinality = trigger_details.get("cardinality")
        msg_arg_value = kwargs.get(trigger_arg_name)

        if (
            cardinality == azure_functions.Cardinality.MANY
            and isinstance(msg_arg_value, list)
            and isinstance(msg_arg_value[0], azure_functions.EventHubEvent)
        ):
            batch_count = str(len(msg_arg_value))
            metadata = getattr(msg_arg_value[0], "metadata", {})
            fully_qualified_namespace = metadata.get("PartitionContext", {}).get("FullyQualifiedNamespace")
            message_id = None

            if config.azure_functions.distributed_tracing:
                for properties in metadata.get("PropertiesArray", []):
                    parent_context = HTTPPropagator.extract(properties)
                    if parent_context.trace_id is not None and parent_context.span_id is not None:
                        ctx.span.link_span(parent_context)
        elif isinstance(msg_arg_value, azure_functions.EventHubEvent):
            batch_count = None
            metadata = getattr(msg_arg_value, "metadata", {})
            fully_qualified_namespace = metadata.get("PartitionContext", {}).get("FullyQualifiedNamespace")
            message_id = metadata.get("SystemProperties", {}).get("message-id")

            if config.azure_functions.distributed_tracing:
                parent_context = HTTPPropagator.extract(metadata.get("Properties", {}))
                if (
                    parent_context is not None
                    and parent_context.trace_id is not None
                    and parent_context.span_id is not None
                ):
                    ctx.span.link_span(parent_context)
        else:
            batch_count = None
            fully_qualified_namespace = None
            message_id = None

        return (
            "azure.functions.event_hubs_trigger_modifier",
            (
                ctx,
                config.azure_functions,
                function_name,
                trigger_type,
                SpanKind.CONSUMER,
                azure_eventhubsx.RECEIVE,
                azure_eventhubsx.SERVICE,
                entity_name,
                fully_qualified_namespace,
                message_id,
                batch_count,
            ),
        )

    return wrap_function_with_tracing(func, context_factory, pre_dispatch=pre_dispatch)


def patched_get_functions(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    try:
        import azure.durable_functions as durable_functions

        durable_patched = getattr(durable_functions, "_datadog_patch", False)
    except Exception:
        durable_patched = False

    functions = wrapped(*args, **kwargs)

    for function in functions:
        trigger = function.get_trigger()
        if not trigger:
            continue

        trigger_type = trigger.get_binding_name()
        trigger_details = trigger.get_dict_repr()
        trigger_arg_name = trigger.name

        function_name = function.get_function_name()
        func = function.get_user_function()

        if trigger_type == "httpTrigger":
            function._func = wrap_http_trigger(pin, func, function_name, trigger_arg_name)
        elif trigger_type == "timerTrigger":
            function._func = wrap_timer_trigger(pin, func, function_name)
        elif trigger_type == "serviceBusTrigger":
            function._func = wrap_service_bus_trigger(pin, func, function_name, trigger_arg_name, trigger_details)
        elif trigger_type == "eventHubTrigger":
            function._func = wrap_event_hubs_trigger(pin, func, function_name, trigger_arg_name, trigger_details)
        elif durable_patched and trigger_type == "activityTrigger":
            function._func = wrap_durable_trigger(
                pin, func, function_name, "Activity", "azure.durable_functions.patched_activity"
            )
        elif durable_patched and trigger_type == "entityTrigger":
            function._func = wrap_durable_trigger(
                pin, func, function_name, "Entity", "azure.durable_functions.patched_entity"
            )
        elif trigger_type == "orchestrationTrigger":
            # Orchestration triggers are explicitly skipped as they are not traced
            continue

    return functions
