"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

`django.apps.registry.Apps.populate` is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""

import asyncio
from inspect import getmro
from inspect import iscoroutinefunction
from inspect import unwrap
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional
from typing import Union
from typing import cast
import weakref

import wrapt
from wrapt.importer import when_imported

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.django.user import _DjangoUserInfoRetriever
from ddtrace.internal import core
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.event_hub import ResultType
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.importlib import func_name
from ddtrace.vendor.packaging.version import parse as parse_version


if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver


log = get_logger(__name__)

DJANGO_TRACING_MINIMAL = asbool(_get_config("DD_DJANGO_TRACING_MINIMAL", default=True))

config._add(
    "django",
    dict(
        _default_service=schematize_service_name("django"),
        cache_service_name=env.get("DD_DJANGO_CACHE_SERVICE_NAME", default="django"),
        database_service_name_prefix=env.get("DD_DJANGO_DATABASE_SERVICE_NAME_PREFIX", default=""),
        database_service_name=env.get("DD_DJANGO_DATABASE_SERVICE_NAME", default=""),
        trace_fetch_methods=asbool(env.get("DD_DJANGO_TRACE_FETCH_METHODS", default=False)),
        distributed_tracing_enabled=True,
        instrument_middleware=asbool(env.get("DD_DJANGO_INSTRUMENT_MIDDLEWARE", default=True)),
        instrument_templates=asbool(env.get("DD_DJANGO_INSTRUMENT_TEMPLATES", default=not DJANGO_TRACING_MINIMAL)),
        instrument_databases=asbool(env.get("DD_DJANGO_INSTRUMENT_DATABASES", default=not DJANGO_TRACING_MINIMAL)),
        instrument_caches=asbool(env.get("DD_DJANGO_INSTRUMENT_CACHES", default=not DJANGO_TRACING_MINIMAL)),
        trace_query_string=None,  # Default to global config
        include_user_name=asm_config._django_include_user_name,
        include_user_email=asm_config._django_include_user_email,
        include_user_login=asm_config._django_include_user_login,
        include_user_realname=asm_config._django_include_user_realname,
        use_handler_with_url_name_resource_format=asbool(
            env.get("DD_DJANGO_USE_HANDLER_WITH_URL_NAME_RESOURCE_FORMAT", default=False)
        ),
        use_handler_resource_format=asbool(env.get("DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT", default=False)),
        use_legacy_resource_format=asbool(env.get("DD_DJANGO_USE_LEGACY_RESOURCE_FORMAT", default=False)),
        trace_asgi_websocket_messages=_get_config(
            "DD_TRACE_WEBSOCKET_MESSAGES_ENABLED",
            default=_get_config("DD_ASGI_TRACE_WEBSOCKET", default=True, modifier=asbool),
            modifier=asbool,
        ),
        asgi_websocket_messages_inherit_sampling=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING", default=True)
        )
        and asbool(_get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)),
        websocket_messages_separate_traces=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)
        ),
        obfuscate_404_resource=env.get("DD_ASGI_OBFUSCATE_404_RESOURCE", default=False),
        views={},
        # DEV: Used only for testing purposes, do not use in production
        _tracer=None,
    ),
)

# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, config.django)


def get_version() -> str:
    import django

    return django.__version__


def _supported_versions() -> dict[str, str]:
    return {"django": ">=2.2.8"}


@trace_utils.with_traced_module
def traced_populate(django, pin, func, instance, args, kwargs):
    """django.apps.registry.Apps.populate is the method used to populate all the apps.

    It is used as a hook to install instrumentation for 3rd party apps (like DRF).

    `populate()` works in 3 phases:

        - Phase 1: Initializes the app configs and imports the app modules.
        - Phase 2: Imports models modules for each app.
        - Phase 3: runs ready() of each app config.

    If all 3 phases successfully run then `instance.ready` will be `True`.
    """

    # populate() can be called multiple times, we don't want to instrument more than once
    if instance.ready:
        log.debug("Django instrumentation already installed, skipping.")
        return func(*args, **kwargs)

    ret = func(*args, **kwargs)

    if not instance.ready:
        log.debug("populate() failed skipping instrumentation.")
        return ret

    settings = django.conf.settings

    # Instrument databases
    if config_django.instrument_databases:
        try:
            from .database import instrument_dbs

            instrument_dbs(django)
        except Exception:
            log.debug("Error instrumenting Django database connections", exc_info=True)

    # Instrument caches
    if config_django.instrument_caches:
        try:
            from .cache import instrument_caches

            instrument_caches(django)
        except Exception:
            log.debug("Error instrumenting Django caches", exc_info=True)

    # Instrument Django Rest Framework if it's installed
    INSTALLED_APPS = getattr(settings, "INSTALLED_APPS", [])

    if "rest_framework" in INSTALLED_APPS:
        try:
            from .restframework import patch_restframework

            patch_restframework(django)
        except Exception:
            log.debug("Error patching rest_framework", exc_info=True)

    # Eager endpoint discovery: walk the ROOT_URLCONF resolver now that apps
    # are ready, so the telemetry "app-endpoints" payload is populated at
    # startup (not only when a request arrives). Dynamic per-tenant urlconfs
    # set by middleware are still picked up by the per-request walk in
    # traced_get_response / traced_get_response_async.
    try:
        from django.urls import get_resolver

        _collect_routes_once(get_resolver(None))
    except Exception:
        log.debug("Error collecting Django routes for endpoint discovery", exc_info=True)

    return ret


def traced_func(django, name, resource=None, ignored_excs=None):
    def wrapped(django, pin, func, instance, args, kwargs):
        tags = {COMPONENT: config_django.integration_name}

        if iscoroutinefunction(func):

            async def _async():
                with (
                    core.context_with_data(
                        "django.func.wrapped", span_name=name, resource=resource, tags=tags, pin=pin
                    ) as ctx,
                    ctx.span,
                ):
                    # Routine ASGI cancellation (e.g. client disconnect on a
                    # streaming response, or a parent timeout) surfaces as
                    # asyncio.CancelledError on the awaited coroutine. The
                    # 4.1.1-shape sync wrapper exited the span before the
                    # await, so this never reached the span; #17404's async
                    # wrapper now brackets the await, so without this guard
                    # every cancelled request tags django.view as errored.
                    # See #17728.
                    ctx.span._ignore_exception(asyncio.CancelledError)
                    core.dispatch(
                        "django.func.wrapped",
                        (
                            args,
                            kwargs,
                            django.core.handlers.wsgi.WSGIRequest if hasattr(django.core.handlers, "wsgi") else object,
                            ctx,
                            ignored_excs,
                        ),
                    )
                    return await func(*args, **kwargs)

            return _async()

        with (
            core.context_with_data("django.func.wrapped", span_name=name, resource=resource, tags=tags, pin=pin) as ctx,
            ctx.span,
        ):
            core.dispatch(
                "django.func.wrapped",
                (
                    args,
                    kwargs,
                    django.core.handlers.wsgi.WSGIRequest if hasattr(django.core.handlers, "wsgi") else object,
                    ctx,
                    ignored_excs,
                ),
            )
            return func(*args, **kwargs)

    return trace_utils.with_traced_module(wrapped)(django)


@trace_utils.with_traced_module
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """
    Patches django.core.handlers.base.BaseHandler.load_middleware to instrument all
    middlewares.
    """
    from ddtrace.contrib.internal.django.middleware import wrap_middleware

    settings_middleware = []
    # Gather all the middleware
    if getattr(django.conf.settings, "MIDDLEWARE", None):
        settings_middleware += django.conf.settings.MIDDLEWARE
    if getattr(django.conf.settings, "MIDDLEWARE_CLASSES", None):
        settings_middleware += django.conf.settings.MIDDLEWARE_CLASSES

    # Iterate over each middleware provided in settings.py
    # Each middleware can either be a function or a class
    for mw_path in settings_middleware:
        mw = django.utils.module_loading.import_string(mw_path)
        wrap_middleware(mw, mw_path)

    return func(*args, **kwargs)


def instrument_view(django, view):
    """
    Helper to wrap Django views.

    We want to wrap all lifecycle/http method functions for every class in the MRO for this view
    """
    if hasattr(view, "__mro__"):
        for cls in reversed(getmro(view)):
            _instrument_view(django, cls)

    return _instrument_view(django, view)


def extract_request_method_list(view):
    try:
        while "view_func" in view.__code__.co_freevars:
            view = view.__closure__[view.__code__.co_freevars.index("view_func")].cell_contents
        if "request_method_list" in view.__code__.co_freevars:
            return view.__closure__[view.__code__.co_freevars.index("request_method_list")].cell_contents
        return []
    except Exception:
        return []


_DEFAULT_METHODS = ("get", "delete", "post", "options", "head")


def _instrument_view(django, view):
    """Helper to wrap Django views."""
    from . import utils

    # All views should be callable, double check before doing anything
    if not callable(view):
        return view

    core.dispatch("service_entrypoint.patch", (unwrap(view),))

    # Patch view HTTP methods and lifecycle methods

    http_method_names = getattr(view, "http_method_names", ())
    request_method_list = extract_request_method_list(view) or http_method_names
    lifecycle_methods = ("setup", "dispatch", "http_method_not_allowed")
    for name in list(request_method_list or _DEFAULT_METHODS) + list(lifecycle_methods):
        try:
            func = getattr(view, name, None)
            if not func or is_wrapted(func):
                continue

            resource = "{0}.{1}".format(func_name(view), name)
            op_name = "django.view.{0}".format(name)
            trace_utils.wrap(view, name, traced_func(django, name=op_name, resource=resource))
        except Exception:
            log.debug("Failed to instrument Django view %r function %s", view, name, exc_info=True)

    # Patch response methods
    response_cls = getattr(view, "response_class", None)
    if response_cls:
        methods = ("render",)
        for name in methods:
            try:
                func = getattr(response_cls, name, None)
                # Do not wrap if the method does not exist or is already wrapped
                if not func or is_wrapted(func):
                    continue

                resource = "{0}.{1}".format(func_name(response_cls), name)
                op_name = "django.response.{0}".format(name)
                trace_utils.wrap(response_cls, name, traced_func(django, name=op_name, resource=resource))
            except Exception:
                log.debug("Failed to instrument Django response %r function %s", response_cls, name, exc_info=True)

    # If the view itself is not wrapped, wrap it
    if not is_wrapted(view):
        view = utils.DjangoViewProxy(
            view, traced_func(django, "django.view", resource=func_name(view), ignored_excs=[django.http.Http404])
        )
    return view


# Resolvers whose url_patterns tree has already been walked for endpoint
# collection by _collect_routes_once(). A WeakSet lets entries auto-drop when
# Django releases a resolver (e.g. after clear_url_caches()), which removes any
# id-reuse risk that a plain set[int] would carry.
_collected_resolvers: "weakref.WeakSet[URLResolver]" = weakref.WeakSet()


def _collect_pattern_methods(callback: Optional[Callable[..., Any]]) -> list[str]:
    """Return the HTTP methods a URLPattern.callback handles, for endpoint collection.

    Extraction semantics: extract_request_method_list walks the wrapper's
    closure chain itself (via the view_func freevar), so the outer callback is
    passed as-is. Unwrapping via __wrapped__ first would peel past the
    require_http_methods wrapper and lose the captured request_method_list,
    collapsing the recorded method list to a wildcard.
    """
    if callback is None:
        return ["*"]
    http_method_names = getattr(callback, "http_method_names", ())
    request_method_list = extract_request_method_list(callback) or http_method_names
    return list(request_method_list) or ["*"]


def _collect_django_routes(patterns: "Iterable[Union[URLPattern, URLResolver]]", prefix: str = "") -> None:
    """Walk URLPattern / URLResolver nodes and register endpoints in endpoint_collection.

    Joins parent and child route segments with the same semantics Django
    itself uses in django.urls.resolvers.URLResolver._join_route for
    request.resolver_match.route: the leading ``^`` of a regex child is
    dropped when appending onto a non-empty prefix, so mixed re_path/path
    trees produce the same route string Django exposes at runtime.
    Non-URLPattern/URLResolver nodes are skipped (e.g. channels URLRouter
    entries slipped into a resolver tree).
    """
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

    for pattern in patterns:
        if not isinstance(pattern, (URLPattern, URLResolver)):
            continue
        segment = str(pattern.pattern)
        if prefix:
            segment = segment.removeprefix("^")
        full_path = prefix + segment
        if isinstance(pattern, URLResolver):
            sub_patterns = getattr(pattern, "url_patterns", None)
            if sub_patterns is None:
                continue
            _collect_django_routes(sub_patterns, prefix=full_path)
        else:
            for method in _collect_pattern_methods(getattr(pattern, "callback", None)):
                endpoint_collection.add_endpoint(method, full_path, operation_name="django.request")


def _collect_routes_once(resolver: "Optional[URLResolver]") -> None:
    """Populate endpoint_collection by walking resolver.url_patterns once per resolver.

    Called from traced_get_response / traced_get_response_async on every
    request. The WeakSet gate makes repeated calls O(1), and naturally handles
    per-request request.urlconf swaps (each distinct urlconf gets its own
    resolver from django.urls.get_resolver, walked on first use). When the
    endpoint-collection flag is off, the walk is skipped entirely — telemetry
    would discard the collected entries anyway, and if the flag is later
    flipped on the WeakSet stays empty so the next request will walk.
    """
    if resolver is None or not asm_config._api_security_endpoint_collection:
        return
    try:
        if resolver in _collected_resolvers:
            return
    except TypeError:
        # Unhashable / unreferenceable resolver shouldn't happen for
        # django.urls.URLResolver, but guard against exotic custom types.
        return
    try:
        patterns = getattr(resolver, "url_patterns", None)
        if patterns is None:
            return
        _collect_django_routes(patterns)
    except Exception:
        log.debug("Failed to walk Django URL resolver for endpoint collection", exc_info=True)
    finally:
        # Mark as collected even on failure so we don't retry forever on a
        # malformed urlconf. A restart recovers; a transient error is a bug
        # we'd rather notice once via log.debug than spam every request.
        try:
            _collected_resolvers.add(resolver)
        except TypeError:
            pass


@trace_utils.with_traced_module
def traced_urls_path(django, pin, wrapped, instance, args, kwargs):
    """Wrapper for url path helpers to ensure all views registered as urls are traced."""
    try:
        view_from_args = False
        view = kwargs.get("view", None)
        if view is None and len(args) > 1:
            view = args[1]
            view_from_args = True

        if view_from_args:
            args = list(args)
            args[1] = instrument_view(django, view)
            args = tuple(args)
        else:
            kwargs["view"] = instrument_view(django, view)
    except Exception:
        log.debug("Failed to instrument Django url path %r %r", args, kwargs, exc_info=True)
    return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_as_view(django, pin, func, instance, args, kwargs):
    """
    Wrapper for django's View.as_view class method
    """
    try:
        instrument_view(django, instance)
    except Exception:
        log.debug("Failed to instrument Django view %r", instance, exc_info=True)
    view = func(*args, **kwargs)
    return wrapt.FunctionWrapper(view, traced_func(django, "django.view", resource=func_name(instance)))


@trace_utils.with_traced_module
def traced_technical_500_response(django, pin, func, instance, args, kwargs):
    """
    Wrapper for django's views.debug.technical_500_response
    """
    response = func(*args, **kwargs)
    try:
        request = get_argument_value(args, kwargs, 0, "request")
        exc_type = get_argument_value(args, kwargs, 1, "exc_type")
        exc_value = get_argument_value(args, kwargs, 2, "exc_value")
        tb = get_argument_value(args, kwargs, 3, "tb")
        core.dispatch("django.technical_500_response", (request, response, exc_type, exc_value, tb))
    except Exception:
        log.debug("Error while trying to trace Django technical 500 response", exc_info=True)
    return response


@trace_utils.with_traced_module
def traced_get_asgi_application(django, pin, func, instance, args, kwargs):
    from ddtrace.contrib.asgi import TraceMiddleware
    from ddtrace.internal.constants import COMPONENT

    def django_asgi_modifier(span, scope):
        span.name = schematize_url_operation("django.request", protocol="http", direction=SpanDirection.INBOUND)
        span._set_attribute(COMPONENT, config_django.integration_name)

    return TraceMiddleware(func(*args, **kwargs), integration_config=config_django, span_modifier=django_asgi_modifier)


@trace_utils.with_traced_module
def traced_login(django, pin, func, instance, args, kwargs):
    func(*args, **kwargs)
    mode = asm_config._user_event_mode
    if mode == "disabled":
        return
    try:
        request = get_argument_value(args, kwargs, 0, "request")
        user = get_argument_value(args, kwargs, 1, "user")
        core.dispatch("django.login", (pin, request, user, mode, _DjangoUserInfoRetriever(user), config_django))
    except Exception:
        log.debug("Error while trying to trace Django login", exc_info=True)


@trace_utils.with_traced_module
def traced_authenticate(django, pin, func, instance, args, kwargs):
    result_user = func(*args, **kwargs)
    mode = asm_config._user_event_mode
    if mode == "disabled":
        return result_user
    try:
        result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
            "django.auth",
            (result_user, mode, kwargs, pin, _DjangoUserInfoRetriever(result_user, credentials=kwargs), config_django),
        ).user
        if result and result.value[0]:
            return result.value[1]
    except Exception:
        log.debug("Error while trying to trace Django authenticate", exc_info=True)

    return result_user


@trace_utils.with_traced_module
def patch_create_user(django, pin, func, instance, args, kwargs):
    user = func(*args, **kwargs)
    core.dispatch(
        "django.create_user", (config_django, pin, func, instance, args, kwargs, user, _DjangoUserInfoRetriever(user))
    )
    return user


def unwrap_views(func, instance, args, kwargs):
    """
    Django channels uses path() and re_path() to route asgi applications. This broke our initial
    assumption that
    django path/re_path/url functions only accept views. Here we unwrap ddtrace view
    instrumentation from asgi
    applications.

    Ex. ``channels.routing.URLRouter([path('', get_asgi_application())])``
    On startup ddtrace.contrib.internal.django.path.instrument_view() will wrap get_asgi_application in a
    DjangoViewProxy.
    Since get_asgi_application is not a django view callback this function will unwrap it.
    """
    from . import utils

    routes = get_argument_value(args, kwargs, 0, "routes")
    for route in routes:
        if isinstance(route.callback, utils.DjangoViewProxy):
            route.callback = route.callback.__wrapped__

    return func(*args, **kwargs)


def _patch(django):
    Pin().onto(django)

    when_imported("django.apps.registry")(lambda m: trace_utils.wrap(m, "Apps.populate", traced_populate(django)))

    if config_django.instrument_middleware:
        when_imported("django.core.handlers.base")(
            lambda m: trace_utils.wrap(m, "BaseHandler.load_middleware", traced_load_middleware(django))
        )

    when_imported("django.core.handlers.wsgi")(lambda m: trace_utils.wrap(m, "WSGIRequest.__init__", wrap_wsgi_environ))
    core.dispatch("django.patch", ())

    @when_imported("django.core.handlers.base")
    def _(m):
        from .response import instrument_module

        instrument_module(django, m)

    @when_imported("django.contrib.auth")
    def _(m):
        trace_utils.wrap(m, "login", traced_login(django))
        trace_utils.wrap(m, "authenticate", traced_authenticate(django))

    # Only wrap get_asgi_application if get_response_async exists. Otherwise we will effectively double-patch
    # because get_response and get_asgi_application will be used. We must rely on the version instead of coalescing
    # with the previous patching hook because of circular imports within `django.core.asgi`.
    if django.VERSION >= (3, 1):
        when_imported("django.core.asgi")(
            lambda m: trace_utils.wrap(m, "get_asgi_application", traced_get_asgi_application(django))
        )

    if config_django.instrument_templates:
        from . import templates

        when_imported("django.template.base")(templates.instrument_module)

    if django.VERSION < (4, 0, 0):
        when_imported("django.conf.urls")(lambda m: trace_utils.wrap(m, "url", traced_urls_path(django)))

    if django.VERSION >= (2, 0, 0):

        @when_imported("django.urls")
        def _(m):
            trace_utils.wrap(m, "path", traced_urls_path(django))
            trace_utils.wrap(m, "re_path", traced_urls_path(django))

    when_imported("django.views.generic.base")(lambda m: trace_utils.wrap(m, "View.as_view", traced_as_view(django)))
    when_imported("django.views.debug")(
        lambda m: trace_utils.wrap(m, "technical_500_response", traced_technical_500_response(django))
    )

    @when_imported("channels.routing")
    def _(m):
        import channels

        channels_version_str = getattr(channels, "__version__", "")
        channels_version = parse_version(channels_version_str)
        if channels_version >= parse_version("3.0"):
            # ASGI3 is only supported in channels v3.0+
            trace_utils.wrap(m, "URLRouter.__init__", unwrap_views)

    when_imported("django.contrib.auth.models")(
        lambda m: trace_utils.wrap(m, "UserManager.create_user", patch_create_user(django))
    )


def wrap_wsgi_environ(wrapped, _instance, args, kwargs):
    result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "django.wsgi_environ", (wrapped, _instance, args, kwargs)
    ).wrapped_result
    # if the callback is registered and runs, return the result
    if result:
        return result.value
    # if the callback is not registered, return the original result
    elif result.response_type == ResultType.RESULT_UNDEFINED:
        return wrapped(*args, **kwargs)
    # if an exception occurs, raise it. It should never happen.
    elif result.exception:
        raise result.exception


def patch():
    import django

    if getattr(django, "_datadog_patch", False):
        return
    _patch(django)

    django._datadog_patch = True


def _unpatch(django):
    trace_utils.unwrap(django.apps.registry.Apps, "populate")
    trace_utils.unwrap(django.core.handlers.base.BaseHandler, "load_middleware")
    trace_utils.unwrap(django.template.base.Template, "render")
    trace_utils.unwrap(django.conf.urls.static, "static")
    trace_utils.unwrap(django.conf.urls, "url")
    trace_utils.unwrap(django.contrib.auth.login, "login")
    trace_utils.unwrap(django.contrib.auth.authenticate, "authenticate")
    trace_utils.unwrap(django.view.debug.technical_500_response, "technical_500_response")
    if django.VERSION >= (2, 0, 0):
        trace_utils.unwrap(django.urls, "path")
        trace_utils.unwrap(django.urls, "re_path")
    trace_utils.unwrap(django.views.generic.base.View, "as_view")
    for conn in django.db.connections.all():
        trace_utils.unwrap(conn, "cursor")
        if hasattr(conn, "get_new_connection"):
            trace_utils.unwrap(conn, "get_new_connection")
    trace_utils.unwrap(django.db.utils.ConnectionHandler, "__getitem__")

    if config.django.instrument_templates:
        from . import templates

        templates.uninstrument_module(django.template.base)

    from .response import uninstrument_module

    uninstrument_module(django, django.core.handlers.base)


def unpatch():
    import django

    if not getattr(django, "_datadog_patch", False):
        return

    _unpatch(django)

    django._datadog_patch = False
