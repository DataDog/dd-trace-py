import asyncio
from inspect import iscoroutinefunction
from inspect import isfunction
from types import FunctionType
from typing import Any
from typing import cast

import ddtrace
from ddtrace.contrib.internal import trace_utils as contrib_trace_utils
from ddtrace.contrib.internal.django.user import _DjangoUserInfoRetriever
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import wrap


log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, ddtrace.config.django)


def traced_middleware_wrapper(mw_path: str, hook: str) -> FunctionType:
    event_name: str = f"django.middleware.{hook}"

    def wrapped_middleware(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
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
            request=request,
        ):
            return func(*args, **kwargs)

    return wrapped_middleware


def traced_process_exception(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
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
        request=request,
    ) as ctx:
        resp = func(*args, **kwargs)

        ctx.set_item("should_set_traceback", hasattr(resp, "status_code") and 500 <= resp.status_code < 600)

        return resp


def traced_auth_middleware_process_request(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
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
        request=request,
    ):
        try:
            return func(*args, **kwargs)
        finally:
            mode = asm_config._user_event_mode
            if mode != "disabled":
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
                    log.debug(
                        "Error while trying to trace Django AuthenticationMiddleware process_request", exc_info=True
                    )


def traced_middleware_factory(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
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
                request=request,
            ) as ctx:
                # See _make_async_traced_middleware_hook / traced_func._async()
                # for the same guard. #17728.
                ctx.span._ignore_exception(asyncio.CancelledError)
                return await middleware(*args, **kwargs)

        return traced_async_middleware_func
    else:
        # Handle sync middleware - use original wrapping approach
        def traced_middleware_func(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
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
                request=request,
            ):
                return func(*args, **kwargs)

        if not is_wrapped(middleware):
            return wrap(middleware, traced_middleware_func)

    return middleware


def _make_async_traced_middleware_hook(mw_path: str, hook: str) -> Any:
    """Create a wrapt-compatible async wrapper for a middleware hook.

    Used instead of bytecode wrapping for async middleware methods, which
    would otherwise cause 'RuntimeError: coroutine ignored GeneratorExit'
    on Python 3.13+. Same pattern as traced_get_response_async in response.py.
    """
    event_name = f"django.middleware.{hook}"

    async def wrapper(func: FunctionType, instance: Any, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
        resource = f"{func_name(instance)}.{hook}"
        request = get_argument_value(args, kwargs, 0, "request", optional=True)
        with core.context_with_data(
            event_name,
            span_name="django.middleware",
            resource=resource,
            tags={COMPONENT: config_django.integration_name},
            request=request,
        ) as ctx:
            # Mirror the patch.py traced_func._async() guard: routine ASGI
            # cancellation surfaces as asyncio.CancelledError; don't tag the
            # middleware span as errored on it. See #17728.
            ctx.span._ignore_exception(asyncio.CancelledError)
            return await func(*args, **kwargs)

    return wrapper


def wrap_middleware_class(mw: type, mw_path: str) -> None:
    for hook in (
        "process_response",
        "process_view",
        "process_template_response",
        "__call__",
    ):
        fn = getattr(mw, hook, None)
        if fn and isfunction(fn) and not is_wrapped(fn):
            if iscoroutinefunction(fn):
                # DEV: Cannot use bytecode wrappers for async methods, otherwise
                # Python 3.13+ raises: RuntimeError: coroutine ignored GeneratorExit
                if not contrib_trace_utils.iswrapped(mw, hook):
                    contrib_trace_utils.wrap(mw, hook, _make_async_traced_middleware_hook(mw_path, hook))
            else:
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
                if iscoroutinefunction(fn):
                    if not contrib_trace_utils.iswrapped(mw, "process_request"):
                        contrib_trace_utils.wrap(
                            mw, "process_request", _make_async_traced_middleware_hook(mw_path, "process_request")
                        )
                else:
                    wrap(fn, traced_middleware_wrapper(mw_path, "process_request"))

    if hasattr(mw, "process_exception"):
        fn = cast(FunctionType, mw.process_exception)
        if iscoroutinefunction(fn):
            if not contrib_trace_utils.iswrapped(mw, "process_exception"):
                contrib_trace_utils.wrap(
                    mw, "process_exception", _make_async_traced_middleware_hook(mw_path, "process_exception")
                )
        elif not is_wrapped_with(fn, traced_process_exception):
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
