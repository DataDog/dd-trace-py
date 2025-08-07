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
from ddtrace.internal.wrapping import wrap
from ddtrace.settings.integration import IntegrationConfig


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, ddtrace.config.django)


def traced_process_request(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    """
    A wrapper for the `process_request` method of Django middleware.
    """
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_request"

    with core.context_with_data(
        "django.middleware.process_request",
        span_name="django.middleware",
        resource=resource,
        tags={
            COMPONENT: config_django.integration_name,
        },
    ):
        return func(*args, **kwargs)


def traced_process_response(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    """
    A wrapper for the `process_response` method of Django middleware.
    """
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_response"

    with core.context_with_data(
        "django.middleware.process_response",
        span_name="django.middleware",
        resource=resource,
        tags={
            COMPONENT: config_django.integration_name,
        },
    ):
        return func(*args, **kwargs)


def traced_process_view(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    """
    A wrapper for the `process_view` method of Django middleware.
    """
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_view"

    with core.context_with_data(
        "django.middleware.process_view",
        span_name="django.middleware",
        resource=resource,
        tags={
            COMPONENT: config_django.integration_name,
        },
    ):
        return func(*args, **kwargs)


def traced_process_template_response(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    """
    A wrapper for the `process_template_response` method of Django middleware.
    """
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_template_response"

    with core.context_with_data(
        "django.middleware.process_template_response",
        span_name="django.middleware",
        resource=resource,
        tags={
            COMPONENT: config_django.integration_name,
        },
    ):
        return func(*args, **kwargs)


def traced_middleware_call(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    """
    A wrapper for the `__call__` method of Django middleware.
    """
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.__call__"

    with core.context_with_data(
        "django.middleware.__call__",
        span_name="django.middleware",
        resource=resource,
        tags={
            COMPONENT: config_django.integration_name,
        },
    ):
        return func(*args, **kwargs)


def traced_process_exception(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    self: Type[Any] = args[0]

    resource = f"{func_name(self)}.process_exception"

    with core.context_with_data(
        "django.middleware.process_exception",
        span_name="django.middleware",
        resource=resource,
        tags={COMPONENT: config_django.integration_name},
    ) as ctx:
        resp = func(*args, **kwargs)

        ctx.set_item("should_set_traceback", hasattr(resp, "status_code") and 500 <= resp.status_code < 600)

        return resp


def traced_middleware_factory(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    middleware = func(*args, **kwargs)

    if isfunction(middleware):
        # DEV: func_name(func) in traced_middleware_factory is not reliable, we'll get "<wrapped>"
        resource = func_name(middleware)

        def traced_middleware_func(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
            with core.context_with_data(
                "django.middleware.func",
                span_name="django.middleware",
                resource=resource,
                tags={
                    COMPONENT: config_django.integration_name,
                },
            ):
                return func(*args, **kwargs)

        if not is_wrapped(middleware, traced_middleware_func):
            return wrap(middleware, traced_middleware_func)

    return middleware


def wrap_middleware_class(mw: type, mw_path: str) -> None:
    if hasattr(mw, "process_request"):
        if mw_path == "django.contrib.auth.middleware.AuthenticationMiddleware":
            # TODO: We need special handling for this process_request method
            pass
        else:
            if not is_wrapped(mw.process_request, traced_process_request):
                wrap(mw.process_request, traced_process_request)

    if hasattr(mw, "process_response") and not is_wrapped(mw.process_response, traced_process_response):
        wrap(mw.process_response, traced_process_response)

    if hasattr(mw, "process_view") and not is_wrapped(mw.process_view, traced_process_view):
        wrap(mw.process_view, traced_process_view)

    if hasattr(mw, "process_template_response") and not is_wrapped(
        mw.process_template_response, traced_process_template_response
    ):
        wrap(mw.process_template_response, traced_process_template_response)

    if hasattr(mw, "__call__") and not is_wrapped(mw.__call__, traced_middleware_call):
        wrap(mw.__call__, traced_middleware_call)

    if hasattr(mw, "process_exception") and not is_wrapped(mw.process_exception, traced_process_exception):
        wrap(mw.process_exception, traced_process_exception)


def wrap_middleware(mw: Any, mw_path: str) -> None:
    """
    Wraps a Django middleware class or function.

    :param mw: The middleware to wrap.
    :param mw_path: The import path of the middleware.
    """
    if isinstance(mw, type):
        wrap_middleware_class(mw, mw_path)
    elif isfunction(mw):
        if not is_wrapped(mw, traced_middleware_factory):
            wrap(mw, traced_middleware_factory)
