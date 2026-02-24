import wrapt

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.molten import MoltenRouterMatchEvent
from ddtrace.contrib._events.molten import MoltenTraceEvent
from ddtrace.internal import core
from ddtrace.internal.utils.importlib import func_name


def trace_wrapped(resource, wrapped, *args, **kwargs):
    with core.context_with_event(
        MoltenTraceEvent(
            function_name=func_name(wrapped),
            component=config.molten.integration_name,
            service=trace_utils.int_service(None, config.molten),
            resource=resource,
        )
    ) as ctx:
        ctx.set_item("allow_default_resource", True)
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
        resource = "{}.{}".format(cname, func.__name__)
        return trace_wrapped(resource, func, *args, **kwargs)


class WrapperRenderer(wrapt.ObjectProxy):
    def render(self, *args, **kwargs):
        func = self.__wrapped__.render
        cname = func_name(self.__wrapped__)
        resource = "{}.{}".format(cname, func.__name__)
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

        if route_and_params is not None:
            route, params = route_and_params
            route.handler = trace_func(func_name(route.handler))(route.handler)
            core.dispatch_event(MoltenRouterMatchEvent(route))
            return route, params
        return route_and_params
