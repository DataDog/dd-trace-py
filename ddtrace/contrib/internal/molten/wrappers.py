import molten
import wrapt

from ddtrace import config
from ddtrace._trace.events import TracingEvent
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.molten import MoltenRouteEvent
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.utils.importlib import func_name


MOLTEN_REQUEST_CONTEXT_KEY = "molten.request.context"


def trace_wrapped(resource, wrapped, *args, **kwargs):
    pin = Pin.get_from(molten)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with core.context_with_event(
        TracingEvent.create(
            operation_name=func_name(wrapped),
            component=config.molten.integration_name,
            integration_config=config.molten,
            service=trace_utils.int_service(pin, config.molten, pin),
            resource=resource,
            span_type="",  # no SpanType matches
            span_kind=SpanKind.SERVER,
            measured=False,
        )
    ):
        return wrapped(*args, **kwargs)


def trace_func(resource):
    @wrapt.function_wrapper
    def _trace_func(wrapped, instance, args, kwargs):
        return trace_wrapped(resource, wrapped, *args, **kwargs)

    return _trace_func


class WrapperComponent(wrapt.ObjectProxy):
    def can_handle_parameter(self, *args, **kwargs):
        func = self.__wrapped__.can_handle_parameter
        cname = func_name(self.__wrapped__)
        resource = f"{cname}.{func.__name__}"
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperRenderer(wrapt.ObjectProxy):
    def render(self, *args, **kwargs):
        func = self.__wrapped__.render
        cname = func_name(self.__wrapped__)
        resource = f"{cname}.{func.__name__}"
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperMiddleware(wrapt.ObjectProxy):
    def __call__(self, *args, **kwargs):
        func = self.__wrapped__.__call__
        resource = func_name(self.__wrapped__)
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperRouter(wrapt.ObjectProxy):
    def match(self, *args, **kwargs):
        func = self.__wrapped__.match
        route_and_params = func(*args, **kwargs)

        pin = Pin.get_from(molten)
        if not pin or not pin.enabled():
            return route_and_params

        if route_and_params is not None:
            route, params = route_and_params
            route.handler = trace_func(func_name(route.handler))(route.handler)

            request_context = core.find_item(MOLTEN_REQUEST_CONTEXT_KEY)
            if request_context is not None:
                core.dispatch_event(
                    MoltenRouteEvent(
                        request_context=request_context,
                        resource="{} {}".format(route.method, route.template),
                        request_route=route.template,
                        route_name=route.name,
                    )
                )

            return route, params
        return route_and_params
