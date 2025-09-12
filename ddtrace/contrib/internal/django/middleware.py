from inspect import iscoroutinefunction
from inspect import isfunction
from types import FunctionType
from typing import Any
from typing import Dict
from typing import Tuple
from typing import cast

import ddtrace
from ddtrace.contrib.internal.django.user import _DjangoUserInfoRetriever
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import wrap
from ddtrace.settings.asm import config as asm_config
from ddtrace.settings.integration import IntegrationConfig


log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, ddtrace.config.django)


def traced_middleware_wrapper(mw_path: str, hook: str) -> FunctionType:
    event_name: str = f"django.middleware.{hook}"

    def wrapped_middleware(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
        self = args[0]
        resource = f"{func_name(self)}.{hook}"

        # The first argument for all middleware is the request object
        # DEV: Do `optional=true` to avoid raising an error for middleware that don't follow the convention
        # DEV: This is a method, so `self` is argument 0 , so request is at position 1
        request = get_argument_value(args, kwargs, 1, "request", optional=True)

        with core.context_with_data(
            event_name,
            span_name="django.middleware",
            resource=resource,
            tags={
                COMPONENT: config_django.integration_name,
            },
            # TODO: Migrate all tests to snapshot tests and remove this
            tracer=config_django._tracer,
            request=request,
        ):
            return func(*args, **kwargs)

    return wrapped_middleware


def traced_process_exception(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    self = args[0]

    resource = f"{func_name(self)}.process_exception"

    # The first argument for all middleware is the request object
    # DEV: Do `optional=true` to avoid raising an error for middleware that don't follow the convention
    # DEV: This is a method, so `self` is argument 0 , so request is at position 1
    request = get_argument_value(args, kwargs, 1, "request", optional=True)

    with core.context_with_data(
        "django.middleware.process_exception",
        span_name="django.middleware",
        resource=resource,
        tags={COMPONENT: config_django.integration_name},
        # TODO: Migrate all tests to snapshot tests and remove this
        tracer=config_django._tracer,
        request=request,
    ) as ctx:
        resp = func(*args, **kwargs)

        ctx.set_item("should_set_traceback", hasattr(resp, "status_code") and 500 <= resp.status_code < 600)

        return resp


def traced_auth_middleware_process_request(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    self = args[0]

    resource = f"{func_name(self)}.process_request"

    # The first argument for all middleware is the request object
    # DEV: Do `optional=true` to avoid raising an error for middleware that don't follow the convention
    # DEV: This is a method, so `self` is argument 0 , so request is at position 1
    request = get_argument_value(args, kwargs, 1, "request", optional=True)

    with core.context_with_data(
        "django.middleware.process_request",
        span_name="django.middleware",
        resource=resource,
        tags={COMPONENT: config_django.integration_name},
        # TODO: Migrate all tests to snapshot tests and remove this
        tracer=config_django._tracer,
        request=request,
    ):
        try:
            return func(*args, **kwargs)
        finally:
            mode = asm_config._user_event_mode
            if mode == "disabled":
                return
            try:
                if request:
                    if hasattr(request, "user") and hasattr(request.user, "_setup"):
                        request.user._setup()
                        request_user = request.user._wrapped
                    else:
                        request_user = request.user
                    if hasattr(request, "session") and hasattr(request.session, "session_key"):
                        session_key = request.session.session_key
                    else:
                        session_key = None
                    core.dispatch(
                        "django.process_request",
                        (
                            request_user,
                            session_key,
                            mode,
                            kwargs,
                            _DjangoUserInfoRetriever(request_user, credentials=kwargs),
                            config_django,
                        ),
                    )
            except Exception:
                log.debug("Error while trying to trace Django AuthenticationMiddleware process_request", exc_info=True)


def traced_middleware_factory(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    middleware = func(*args, **kwargs)

    if not isfunction(middleware):
        return middleware

    if hasattr(func, "__module__") and hasattr(func, "__qualname__"):
        resource = f"{func.__module__}.{func.__qualname__}"
    else:
        resource = func_name(func)

    if iscoroutinefunction(middleware):
        # Handle async middleware - create async wrapper
        async def traced_async_middleware_func(*args, **kwargs):
            # The first argument for all middleware is the request object
            # DEV: Do `optional=true` to avoid raising an error for middleware that don't follow the convention
            # DEV: This is a function, so no `self` argument, so request is at position 0
            request = get_argument_value(args, kwargs, 0, "request", optional=True)

            with core.context_with_data(
                "django.middleware.func",
                span_name="django.middleware",
                resource=resource,
                tags={
                    COMPONENT: config_django.integration_name,
                },
                tracer=config_django._tracer,
                request=request,
            ):
                return await middleware(*args, **kwargs)

        return traced_async_middleware_func
    else:
        # Handle sync middleware - use original wrapping approach
        def traced_middleware_func(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
            # The first argument for all middleware is the request object
            # DEV: Do `optional=true` to avoid raising an error for middleware that don't follow the convention
            # DEV: This is a function, so no `self` argument, so request is at position 0
            request = get_argument_value(args, kwargs, 0, "request", optional=True)

            with core.context_with_data(
                "django.middleware.func",
                span_name="django.middleware",
                resource=resource,
                tags={
                    COMPONENT: config_django.integration_name,
                },
                # TODO: Migrate all tests to snapshot tests and remove this
                tracer=config_django._tracer,
                request=request,
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
            if not is_wrapped_with(mw.process_request, traced_auth_middleware_process_request):
                wrap(
                    mw.process_request,
                    traced_auth_middleware_process_request,
                )
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
