from inspect import isfunction
from types import FunctionType
from typing import Any
from typing import Dict
from typing import Tuple
from typing import Type
from typing import cast

import ddtrace
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import wrap
from ddtrace.settings.integration import IntegrationConfig


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, ddtrace.config.django)


def traced_middleware_wrapper(mw_path: str, hook: str) -> FunctionType:
    event_name: str = f"django.middleware.{hook}"

    def wrapped_middleware(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
        self: Type[Any] = cast(Type[Any], args[0])
        resource = f"{func_name(self)}.{hook}"

        with core.context_with_data(
            event_name,
            span_name="django.middleware",
            resource=resource,
            tags={
                COMPONENT: config_django.integration_name,
            },
            # TODO: Migrate all tests to snapshot tests and remove this
            tracer=config_django._tracer,
        ):
            return func(*args, **kwargs)

    return wrapped_middleware


def traced_process_exception(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_exception"

    with core.context_with_data(
        "django.middleware.process_exception",
        span_name="django.middleware",
        resource=resource,
        tags={COMPONENT: config_django.integration_name},
        # TODO: Migrate all tests to snapshot tests and remove this
        tracer=config_django._tracer,
    ) as ctx:
        resp = func(*args, **kwargs)

        ctx.set_item("should_set_traceback", hasattr(resp, "status_code") and 500 <= resp.status_code < 600)

        return resp


def traced_middleware_factory(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    middleware = func(*args, **kwargs)

    if isfunction(middleware):
        if hasattr(func, "__module__") and hasattr(func, "__qualname__"):
            resource = f"{func.__module__}.{func.__qualname__}"
        else:
            resource = func_name(func)

        def traced_middleware_func(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
            with core.context_with_data(
                "django.middleware.func",
                span_name="django.middleware",
                resource=resource,
                tags={
                    COMPONENT: config_django.integration_name,
                },
                # TODO: Migrate all tests to snapshot tests and remove this
                tracer=config_django._tracer,
            ):
                return func(*args, **kwargs)

        if not is_wrapped(middleware):
            return wrap(middleware, traced_middleware_func)

    return middleware


def wrap_middleware_class(mw: type, mw_path: str) -> None:
    for hook in (
        "process_response",
        "process_view",
        "process_template_response",
        "__call__",
    ):
        fn = getattr(mw, hook, None)
        if fn and isfunction(fn) and not is_wrapped(fn):
            wrap(fn, traced_middleware_wrapper(mw_path, hook))

    # Special handling for process_request and process_exception

    if hasattr(mw, "process_request"):
        if mw_path == "django.contrib.auth.middleware.AuthenticationMiddleware":
            # TODO: We need special handling for this process_request method
            pass
        else:
            fn = cast(FunctionType, mw.process_request)
            if not is_wrapped(fn):
                wrap(fn, traced_middleware_wrapper(mw_path, "process_request"))

    if hasattr(mw, "process_exception"):
        fn = cast(FunctionType, mw.process_exception)
        if not is_wrapped_with(fn, traced_process_exception):
            wrap(fn, traced_process_exception)


def wrap_middleware(mw: Any, mw_path: str) -> None:
    """
    Wraps a Django middleware class or function.

    :param mw: The middleware to wrap.
    :param mw_path: The import path of the middleware.
    """
    if isinstance(mw, type):
        wrap_middleware_class(mw, mw_path)
    elif isfunction(mw) and not is_wrapped_with(mw, traced_middleware_factory):
        wrap(mw, traced_middleware_factory)
