"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

`django.apps.registry.Apps.populate` is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""
import sys

from inspect import isclass, isfunction

from ddtrace import config, Pin
from ddtrace.vendor import debtcollector, six, wrapt
from ddtrace.compat import getattr_static
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib import func_name, dbapi
from ddtrace.ext import http, sql as sqlx, SpanTypes
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.utils import from_wsgi_header
from ddtrace.utils.formats import asbool, get_env
from ddtrace.utils.wrappers import unwrap, iswrapped

from .compat import get_resolver, user_is_authenticated
from . import utils, conf


wrap = wrapt.wrap_function_wrapper
log = get_logger(__name__)

config._add(
    "django",
    dict(
        service_name=config._get_service(default="django"),
        cache_service_name=get_env("django", "cache_service_name") or "django",
        database_service_name_prefix=get_env("django", "database_service_name_prefix", default=""),
        distributed_tracing_enabled=True,
        instrument_middleware=asbool(get_env("django", "instrument_middleware", default=True)),
        instrument_databases=True,
        instrument_caches=True,
        analytics_enabled=None,  # None allows the value to be overridden by the global config
        analytics_sample_rate=None,
        trace_query_string=None,  # Default to global config
        include_user_name=True,
    ),
)

propagator = HTTPPropagator()


def with_traced_module(func):
    """Helper for providing tracing essentials (module and pin) for tracing
    wrappers.

    This helper enables tracing wrappers to dynamically be disabled when the
    corresponding pin is disabled.

    Usage::

        @with_traced_module
        def my_traced_wrapper(django, pin, func, instance, args, kwargs):
            # Do tracing stuff
            pass

        def patch():
            import django
            wrap(django.somefunc, my_traced_wrapper(django))
    """

    def with_mod(mod):
        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin._find(instance, mod)
            if pin and not pin.enabled():
                return wrapped(*args, **kwargs)
            elif not pin:
                log.debug("Pin not found for traced method %r", wrapped)
                return wrapped(*args, **kwargs)
            return func(mod, pin, wrapped, instance, args, kwargs)

        return wrapper

    return with_mod


def patch_conn(django, conn):
    def cursor(django, pin, func, instance, args, kwargs):
        database_prefix = config.django.database_service_name_prefix
        alias = getattr(conn, "alias", "default")
        service = "{}{}{}".format(database_prefix, alias, "db")
        vendor = getattr(conn, "vendor", "db")
        prefix = sqlx.normalize_vendor(vendor)
        tags = {
            "django.db.vendor": vendor,
            "django.db.alias": alias,
        }
        pin = Pin(service, tags=tags, tracer=pin.tracer, app=prefix)
        return dbapi.TracedCursor(func(*args, **kwargs), pin)

    if not isinstance(conn.cursor, wrapt.ObjectProxy):
        conn.cursor = wrapt.FunctionWrapper(conn.cursor, with_traced_module(cursor)(django))


def instrument_dbs(django):
    def all_connections(wrapped, instance, args, kwargs):
        conns = wrapped(*args, **kwargs)
        for conn in conns:
            try:
                patch_conn(django, conn)
            except Exception:
                log.debug("Error instrumenting database connection %r", conn, exc_info=True)
        return conns

    if not isinstance(django.db.connections.all, wrapt.ObjectProxy):
        django.db.connections.all = wrapt.FunctionWrapper(django.db.connections.all, all_connections)

    if hasattr(django.db, "connection") and not isinstance(django.db.connection.cursor, wrapt.ObjectProxy):
        patch_conn(django, django.db.connection)


def _set_request_tags(django, span, request):
    span.set_tag("django.request.class", func_name(request))
    span.set_tag(http.METHOD, request.method)

    if django.VERSION >= (2, 2, 0):
        headers = request.headers
    else:
        headers = {}
        for header, value in request.META.items():
            name = from_wsgi_header(header)
            if name:
                headers[name] = value

    store_request_headers(headers, span, config.django)

    user = getattr(request, "user", None)
    if user is not None:
        if hasattr(user, "is_authenticated"):
            span.set_tag("django.user.is_authenticated", user_is_authenticated(user))

        uid = getattr(user, "pk", None)
        if uid:
            span.set_tag("django.user.id", uid)

        if config.django.include_user_name:
            username = getattr(user, "username", None)
            if username:
                span.set_tag("django.user.name", username)


@with_traced_module
def traced_cache(django, pin, func, instance, args, kwargs):
    if not config.django.instrument_caches:
        return func(*args, **kwargs)

    # get the original function method
    with pin.tracer.trace("django.cache", span_type=SpanTypes.CACHE, service=config.django.cache_service_name) as span:
        # update the resource name and tag the cache backend
        span.resource = utils.resource_from_cache_prefix(func_name(func), instance)
        cache_backend = "{}.{}".format(instance.__module__, instance.__class__.__name__)
        span.set_tag("django.cache.backend", cache_backend)

        if args:
            keys = utils.quantize_key_values(args[0])
            span.set_tag("django.cache.key", keys)

        return func(*args, **kwargs)


def instrument_caches(django):
    cache_backends = set([cache["BACKEND"] for cache in django.conf.settings.CACHES.values()])
    for cache_path in cache_backends:
        split = cache_path.split(".")
        cache_module = ".".join(split[:-1])
        cache_cls = split[-1]
        for method in ["get", "set", "add", "delete", "incr", "decr", "get_many", "set_many", "delete_many"]:
            try:
                cls = django.utils.module_loading.import_string(cache_path)
                # DEV: this can be removed when we add an idempotent `wrap`
                if not iswrapped(cls, method):
                    wrap(cache_module, "{0}.{1}".format(cache_cls, method), traced_cache(django))
            except Exception:
                log.debug("Error instrumenting cache %r", cache_path, exc_info=True)


@with_traced_module
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

    if hasattr(settings, "DATADOG_TRACE"):
        debtcollector.deprecate(("Using DATADOG_TRACE Django settings are no longer supported. "))
        conf.configure_from_settings(pin, config.django, settings.DATADOG_TRACE)

    # Instrument databases
    if config.django.instrument_databases:
        try:
            instrument_dbs(django)
        except Exception:
            log.debug("Error instrumenting Django database connections", exc_info=True)

    # Instrument caches
    if config.django.instrument_caches:
        try:
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

    return ret


def traced_func(django, name, resource=None):
    """Returns a function to trace Django functions."""

    def wrapped(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace(name, resource=resource):
            return func(*args, **kwargs)

    return with_traced_module(wrapped)(django)


def traced_process_exception(django, name, resource=None):
    def wrapped(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace(name, resource=resource) as span:
            resp = func(*args, **kwargs)

            # If the response code is erroneous then grab the traceback
            # and set an error.
            if hasattr(resp, "status_code") and 500 <= resp.status_code < 600:
                span.set_traceback()
            return resp

    return with_traced_module(wrapped)(django)


@with_traced_module
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """Patches django.core.handlers.base.BaseHandler.load_middleware to instrument all middlewares."""
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

        # Instrument function-based middleware
        if isfunction(mw) and not iswrapped(mw):
            split = mw_path.split(".")
            if len(split) < 2:
                continue
            base = ".".join(split[:-1])
            attr = split[-1]

            # Function-based middleware is a factory which returns a handler function for requests.
            # So instead of tracing the factory, we want to trace its returned value.
            # We wrap the factory to return a traced version of the handler function.
            def wrapped_factory(func, instance, args, kwargs):
                # r is the middleware handler function returned from the factory
                r = func(*args, **kwargs)
                return wrapt.FunctionWrapper(r, traced_func(django, "django.middleware", resource=mw_path))

            wrap(base, attr, wrapped_factory)

        # Instrument class-based middleware
        elif isclass(mw):
            for hook in [
                "process_request",
                "process_response",
                "process_view",
                "process_template_response",
                "__call__",
            ]:
                if hasattr(mw, hook) and not iswrapped(mw, hook):
                    wrap(mw, hook, traced_func(django, "django.middleware", resource=mw_path + ".{0}".format(hook)))
            # Do a little extra for `process_exception`
            if hasattr(mw, "process_exception") and not iswrapped(mw, "process_exception"):
                res = mw_path + ".{0}".format("process_exception")
                wrap(mw, "process_exception", traced_process_exception(django, "django.middleware", resource=res))

    return func(*args, **kwargs)


@with_traced_module
def traced_get_response(django, pin, func, instance, args, kwargs):
    """Trace django.core.handlers.base.BaseHandler.get_response() (or other implementations).

    This is the main entry point for requests.

    Django requests are handled by a Handler.get_response method (inherited from base.BaseHandler).
    This method invokes the middleware chain and returns the response generated by the chain.
    """

    request = kwargs.get("request", args[0])
    if request is None:
        return func(*args, **kwargs)

    try:
        request_headers = request.META

        if config.django.distributed_tracing_enabled:
            context = propagator.extract(request_headers)
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        # Determine the resolver and resource name for this request
        resolver = get_resolver(getattr(request, "urlconf", None))

        if django.VERSION < (1, 10, 0):
            error_type_404 = django.core.urlresolvers.Resolver404
        else:
            error_type_404 = django.urls.exceptions.Resolver404

        route = None
        resolver_match = None
        resource = request.method
        try:
            # Resolve the requested url
            resolver_match = resolver.resolve(request.path_info)

            # Determine the resource name to use
            # In Django >= 2.2.0 we can access the original route or regex pattern
            if django.VERSION >= (2, 2, 0):
                route = utils.get_django_2_route(resolver, resolver_match)
                if route:
                    resource = "{0} {1}".format(request.method, route)
                else:
                    resource = request.method
            # Older versions just use the view/handler name, e.g. `views.MyView.handler`
            else:
                # TODO: Validate if `resolver.pattern.regex.pattern` is available or not
                callback, callback_args, callback_kwargs = resolver_match
                resource = "{0} {1}".format(request.method, func_name(callback))

        except error_type_404:
            # Normalize all 404 requests into a single resource name
            # DEV: This is for potential cardinality issues
            resource = "{0} 404".format(request.method)
        except Exception:
            log.debug(
                "Failed to resolve request path %r with path info %r",
                request,
                getattr(request, "path_info", "not-set"),
                exc_info=True,
            )
    except Exception:
        log.debug("Failed to trace django request %r", args, exc_info=True)
        return func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "django.request", resource=resource, service=config.django["service_name"], span_type=SpanTypes.HTTP
        ) as span:
            analytics_sr = config.django.get_analytics_sample_rate(use_global_config=True)
            if analytics_sr is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

            if config.django.http.trace_query_string:
                span.set_tag(http.QUERY_STRING, request_headers["QUERY_STRING"])

            # Not a 404 request
            if resolver_match:
                span.set_tag("django.view", resolver_match.view_name)
                utils.set_tag_array(span, "django.namespace", resolver_match.namespaces)

                # Django >= 2.0.0
                if hasattr(resolver_match, "app_names"):
                    utils.set_tag_array(span, "django.app", resolver_match.app_names)

            if route:
                span.set_tag("http.route", route)

            # Set HTTP Request tags
            span.set_tag(http.URL, utils.get_request_uri(request))

            response = func(*args, **kwargs)

            # Note: this call must be done after the function call because
            # some attributes (like `user`) are added to the request through
            # the middleware chain
            _set_request_tags(django, span, request)

            if response:
                span.set_tag(http.STATUS_CODE, response.status_code)
                if 500 <= response.status_code < 600:
                    span.error = 1
                span.set_tag("django.response.class", func_name(response))
                if hasattr(response, "template_name"):
                    # template_name is a bit of a misnomer, as it could be any of:
                    # a list of strings, a tuple of strings, a single string, or an instance of Template
                    # for more detail, see:
                    # https://docs.djangoproject.com/en/3.0/ref/template-response/#django.template.response.SimpleTemplateResponse.template_name
                    template = response.template_name

                    if isinstance(template, six.string_types):
                        template_names = [template]
                    elif isinstance(template, (list, tuple,)):
                        template_names = template
                    elif hasattr(template, "template"):
                        # ^ checking by attribute here because
                        # django backend implementations don't have a common base
                        # `.template` is also the most consistent across django versions
                        template_names = [template.template.name]
                    else:
                        template_names = None

                    utils.set_tag_array(span, "django.response.template", template_names)

                headers = dict(response.items())
                store_response_headers(headers, span, config.django)

            return response


@with_traced_module
def traced_template_render(django, pin, wrapped, instance, args, kwargs):
    """Instrument django.template.base.Template.render for tracing template rendering."""
    template_name = getattr(instance, "name", None)
    if template_name:
        resource = template_name
    else:
        resource = "{0}.{1}".format(func_name(instance), wrapped.__name__)

    with pin.tracer.trace("django.template.render", resource=resource, span_type=http.TEMPLATE) as span:
        if template_name:
            span.set_tag("django.template.name", template_name)
        engine = getattr(instance, "engine", None)
        if engine:
            span.set_tag("django.template.engine.class", func_name(engine))

        return wrapped(*args, **kwargs)


def instrument_view(django, view):
    """Helper to wrap Django views."""
    # All views should be callable, double check before doing anything
    if not callable(view) or isinstance(view, wrapt.ObjectProxy):
        return view

    # Patch view HTTP methods and lifecycle methods
    http_method_names = getattr(view, "http_method_names", ("get", "delete", "post", "options", "head"))
    lifecycle_methods = ("setup", "dispatch", "http_method_not_allowed")
    for name in list(http_method_names) + list(lifecycle_methods):
        try:
            # View methods can be staticmethods
            func = getattr_static(view, name, None)
            if not func or isinstance(func, wrapt.ObjectProxy):
                continue

            resource = "{0}.{1}".format(func_name(view), name)
            op_name = "django.view.{0}".format(name)

            # Set attribute here rather than using wrapt.wrappers.wrap_function_wrapper
            # since it will not resolve attribute to staticmethods
            wrapper = wrapt.FunctionWrapper(func, traced_func(django, name=op_name, resource=resource))
            setattr(view, name, wrapper)
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
                if not func or isinstance(func, wrapt.ObjectProxy):
                    continue

                resource = "{0}.{1}".format(func_name(response_cls), name)
                op_name = "django.response.{0}".format(name)
                wrap(response_cls, name, traced_func(django, name=op_name, resource=resource))
            except Exception:
                log.debug("Failed to instrument Django response %r function %s", response_cls, name, exc_info=True)

    # Return a wrapped version of this view
    return wrapt.FunctionWrapper(view, traced_func(django, "django.view", resource=func_name(view)))


@with_traced_module
def traced_urls_path(django, pin, wrapped, instance, args, kwargs):
    """Wrapper for url path helpers to ensure all views registered as urls are traced."""
    try:
        if "view" in kwargs:
            kwargs["view"] = instrument_view(django, kwargs["view"])
        elif len(args) >= 2:
            args = list(args)
            args[1] = instrument_view(django, args[1])
            args = tuple(args)
    except Exception:
        log.debug("Failed to instrument Django url path %r %r", args, kwargs, exc_info=True)
    return wrapped(*args, **kwargs)


@with_traced_module
def traced_as_view(django, pin, func, instance, args, kwargs):
    """
    Wrapper for django's View.as_view class method
    """
    try:
        instrument_view(django, instance)
    except Exception:
        log.debug("Failed to instrument Django view %r", instance, exc_info=True)
    view = func(*args, **kwargs)
    return wrapt.FunctionWrapper(view, traced_func(django, "django.view", resource=func_name(view)))


def _patch(django):
    Pin(service=config.django["service_name"]).onto(django)
    wrap(django, "apps.registry.Apps.populate", traced_populate(django))

    # DEV: this check will be replaced with import hooks in the future
    if "django.core.handlers.base" not in sys.modules:
        import django.core.handlers.base

    if config.django.instrument_middleware:
        wrap(django, "core.handlers.base.BaseHandler.load_middleware", traced_load_middleware(django))

    wrap(django, "core.handlers.base.BaseHandler.get_response", traced_get_response(django))

    # DEV: this check will be replaced with import hooks in the future
    if "django.template.base" not in sys.modules:
        import django.template.base
    wrap(django, "template.base.Template.render", traced_template_render(django))

    # DEV: this check will be replaced with import hooks in the future
    if "django.conf.urls.static" not in sys.modules:
        import django.conf.urls.static
    wrap(django, "conf.urls.url", traced_urls_path(django))
    if django.VERSION >= (2, 0, 0):
        wrap(django, "urls.path", traced_urls_path(django))
        wrap(django, "urls.re_path", traced_urls_path(django))

    # DEV: this check will be replaced with import hooks in the future
    if "django.views.generic.base" not in sys.modules:
        import django.views.generic.base
    wrap(django, "views.generic.base.View.as_view", traced_as_view(django))


def patch():
    # DEV: this import will eventually be replaced with the module given from an import hook
    import django

    if getattr(django, "_datadog_patch", False):
        return
    _patch(django)

    setattr(django, "_datadog_patch", True)


def _unpatch(django):
    unwrap(django.apps.registry.Apps, "populate")
    unwrap(django.core.handlers.base.BaseHandler, "load_middleware")
    unwrap(django.core.handlers.base.BaseHandler, "get_response")
    unwrap(django.template.base.Template, "render")
    unwrap(django.conf.urls.static, "static")
    unwrap(django.conf.urls, "url")
    if django.VERSION >= (2, 0, 0):
        unwrap(django.urls, "path")
        unwrap(django.urls, "re_path")
    unwrap(django.views.generic.base.View, "as_view")
    for conn in django.db.connections.all():
        unwrap(conn, "cursor")
    unwrap(django.db.connections, "all")


def unpatch():
    import django

    if not getattr(django, "_datadog_patch", False):
        return

    _unpatch(django)

    setattr(django, "_datadog_patch", False)
