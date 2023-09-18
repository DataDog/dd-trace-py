"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

`django.apps.registry.Apps.populate` is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""
import functools
from inspect import getmro
from inspect import isclass
from inspect import isfunction
import os

import wrapt
from wrapt.importer import when_imported

from ddtrace import Pin
from ddtrace import config
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants as _asm_constants
from ddtrace.appsec import _utils as appsec_utils
from ddtrace.appsec.trace_utils import track_user_login_failure_event
from ddtrace.appsec.trace_utils import track_user_login_success_event
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import dbapi
from ddtrace.contrib import func_name
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import http
from ddtrace.ext import sql as sqlx
from ddtrace.internal import core
from ddtrace.internal.compat import Iterable
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.integration import IntegrationConfig

from .. import trace_utils
from ...appsec._constants import WAF_CONTEXT_NAMES
from ...internal.utils import get_argument_value
from ..trace_utils import _get_request_header_user_agent
from ..trace_utils import _set_url_tag


log = get_logger(__name__)

config._add(
    "django",
    dict(
        _default_service=schematize_service_name("django"),
        cache_service_name=os.getenv("DD_DJANGO_CACHE_SERVICE_NAME", default="django"),
        database_service_name_prefix=os.getenv("DD_DJANGO_DATABASE_SERVICE_NAME_PREFIX", default=""),
        database_service_name=os.getenv("DD_DJANGO_DATABASE_SERVICE_NAME", default=""),
        trace_fetch_methods=asbool(os.getenv("DD_DJANGO_TRACE_FETCH_METHODS", default=False)),
        distributed_tracing_enabled=True,
        instrument_middleware=asbool(os.getenv("DD_DJANGO_INSTRUMENT_MIDDLEWARE", default=True)),
        instrument_templates=asbool(os.getenv("DD_DJANGO_INSTRUMENT_TEMPLATES", default=True)),
        instrument_databases=asbool(os.getenv("DD_DJANGO_INSTRUMENT_DATABASES", default=True)),
        instrument_caches=asbool(os.getenv("DD_DJANGO_INSTRUMENT_CACHES", default=True)),
        analytics_enabled=None,  # None allows the value to be overridden by the global config
        analytics_sample_rate=None,
        trace_query_string=None,  # Default to global config
        include_user_name=asbool(os.getenv("DD_DJANGO_INCLUDE_USER_NAME", default=True)),
        use_handler_with_url_name_resource_format=asbool(
            os.getenv("DD_DJANGO_USE_HANDLER_WITH_URL_NAME_RESOURCE_FORMAT", default=False)
        ),
        use_handler_resource_format=asbool(os.getenv("DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT", default=False)),
        use_legacy_resource_format=asbool(os.getenv("DD_DJANGO_USE_LEGACY_RESOURCE_FORMAT", default=False)),
    ),
)

_NotSet = object()
psycopg_cursor_cls = Psycopg2TracedCursor = Psycopg3TracedCursor = _NotSet


def get_version():
    # type: () -> str
    import django

    return django.__version__


def patch_conn(django, conn):
    global psycopg_cursor_cls, Psycopg2TracedCursor, Psycopg3TracedCursor

    if psycopg_cursor_cls is _NotSet:
        try:
            from psycopg.cursor import Cursor as psycopg_cursor_cls

            from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor
        except ImportError:
            Psycopg3TracedCursor = None
            try:
                from psycopg2._psycopg import cursor as psycopg_cursor_cls

                from ddtrace.contrib.psycopg.cursor import Psycopg2TracedCursor
            except ImportError:
                psycopg_cursor_cls = None
                Psycopg2TracedCursor = None

    def cursor(django, pin, func, instance, args, kwargs):
        alias = getattr(conn, "alias", "default")

        if config.django.database_service_name:
            service = config.django.database_service_name
        else:
            database_prefix = config.django.database_service_name_prefix
            service = "{}{}{}".format(database_prefix, alias, "db")
            service = schematize_service_name(service)

        vendor = getattr(conn, "vendor", "db")
        prefix = sqlx.normalize_vendor(vendor)
        tags = {
            "django.db.vendor": vendor,
            "django.db.alias": alias,
        }
        pin = Pin(service, tags=tags, tracer=pin.tracer)
        cursor = func(*args, **kwargs)
        traced_cursor_cls = dbapi.TracedCursor
        try:
            if cursor.cursor.__class__.__module__.startswith("psycopg2."):
                # Import lazily to avoid importing psycopg2 if not already imported.
                from ddtrace.contrib.psycopg.cursor import Psycopg2TracedCursor

                traced_cursor_cls = Psycopg2TracedCursor
            elif type(cursor.cursor).__name__ == "Psycopg3TracedCursor":
                # Import lazily to avoid importing psycopg if not already imported.
                from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor

                traced_cursor_cls = Psycopg3TracedCursor
        except AttributeError:
            pass

        # Each db alias will need its own config for dbapi
        cfg = IntegrationConfig(
            config.django.global_config,  # global_config needed for analytics sample rate
            "{}-{}".format("django", alias),  # name not used but set anyway
            _default_service=config.django._default_service,
            _dbapi_span_name_prefix=prefix,
            trace_fetch_methods=config.django.trace_fetch_methods,
            analytics_enabled=config.django.analytics_enabled,
            analytics_sample_rate=config.django.analytics_sample_rate,
        )
        return traced_cursor_cls(cursor, pin, cfg)

    if not isinstance(conn.cursor, wrapt.ObjectProxy):
        conn.cursor = wrapt.FunctionWrapper(conn.cursor, trace_utils.with_traced_module(cursor)(django))


def instrument_dbs(django):
    def get_connection(wrapped, instance, args, kwargs):
        conn = wrapped(*args, **kwargs)
        try:
            patch_conn(django, conn)
        except Exception:
            log.debug("Error instrumenting database connection %r", conn, exc_info=True)
        return conn

    if not isinstance(django.db.utils.ConnectionHandler.__getitem__, wrapt.ObjectProxy):
        django.db.utils.ConnectionHandler.__getitem__ = wrapt.FunctionWrapper(
            django.db.utils.ConnectionHandler.__getitem__, get_connection
        )


@trace_utils.with_traced_module
def traced_cache(django, pin, func, instance, args, kwargs):
    from . import utils

    if not config.django.instrument_caches:
        return func(*args, **kwargs)

    # get the original function method
    with pin.tracer.trace("django.cache", span_type=SpanTypes.CACHE, service=config.django.cache_service_name) as span:
        span.set_tag_str(COMPONENT, config.django.integration_name)

        # update the resource name and tag the cache backend
        span.resource = utils.resource_from_cache_prefix(func_name(func), instance)
        cache_backend = "{}.{}".format(instance.__module__, instance.__class__.__name__)
        span.set_tag_str("django.cache.backend", cache_backend)

        if args:
            # Key can be a list of strings, an individual string, or a dict
            # Quantize will ensure we have a space separated list of keys
            keys = utils.quantize_key_values(args[0])
            span.set_tag_str("django.cache.key", keys)

        result = func(*args, **kwargs)
        command_name = func.__name__
        if command_name == "get_many":
            span.set_metric(
                db.ROWCOUNT, sum(1 for doc in result if doc) if result and isinstance(result, Iterable) else 0
            )
        elif command_name == "get":
            try:
                # check also for special case for Django~3.2 that returns an empty Sentinel
                # object for empty results
                # also check if result is Iterable first since some iterables return ambiguous
                # truth results with ``==``
                if result is None or (
                    not isinstance(result, Iterable) and result == getattr(instance, "_missing_key", None)
                ):
                    span.set_metric(db.ROWCOUNT, 0)
                else:
                    span.set_metric(db.ROWCOUNT, 1)
            except (AttributeError, NotImplementedError, ValueError):
                span.set_metric(db.ROWCOUNT, 0)
        return result


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
                if not trace_utils.iswrapped(cls, method):
                    trace_utils.wrap(cache_module, "{0}.{1}".format(cache_cls, method), traced_cache(django))
            except Exception:
                log.debug("Error instrumenting cache %r", cache_path, exc_info=True)


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


def traced_func(django, name, resource=None, ignored_excs=None):
    """Returns a function to trace Django functions."""

    def wrapped(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace(name, resource=resource) as s:
            s.set_tag_str(COMPONENT, config.django.integration_name)

            if ignored_excs:
                for exc in ignored_excs:
                    s._ignore_exception(exc)
            core.dispatch(
                "django.func.wrapped",
                args,
                kwargs,
                django.core.handlers.wsgi.WSGIRequest if hasattr(django.core.handlers, "wsgi") else object,
            )
            return func(*args, **kwargs)

    return trace_utils.with_traced_module(wrapped)(django)


def traced_process_exception(django, name, resource=None):
    def wrapped(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace(name, resource=resource) as span:
            span.set_tag_str(COMPONENT, config.django.integration_name)

            resp = func(*args, **kwargs)

            # If the response code is erroneous then grab the traceback
            # and set an error.
            if hasattr(resp, "status_code") and 500 <= resp.status_code < 600:
                span.set_traceback()
            return resp

    return trace_utils.with_traced_module(wrapped)(django)


@trace_utils.with_traced_module
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """
    Patches django.core.handlers.base.BaseHandler.load_middleware to instrument all
    middlewares.
    """
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
        if isfunction(mw) and not trace_utils.iswrapped(mw):
            split = mw_path.split(".")
            if len(split) < 2:
                continue
            base = ".".join(split[:-1])
            attr = split[-1]

            # DEV: We need to have a closure over `mw_path` for the resource name or else
            # all function based middleware will share the same resource name
            def _wrapper(resource):
                # Function-based middleware is a factory which returns a handler function for
                # requests.
                # So instead of tracing the factory, we want to trace its returned value.
                # We wrap the factory to return a traced version of the handler function.
                def wrapped_factory(func, instance, args, kwargs):
                    # r is the middleware handler function returned from the factory
                    r = func(*args, **kwargs)
                    if r:
                        return wrapt.FunctionWrapper(
                            r,
                            traced_func(django, "django.middleware", resource=resource),
                        )
                    # If r is an empty middleware function (i.e. returns None), don't wrap since
                    # NoneType cannot be called
                    else:
                        return r

                return wrapped_factory

            trace_utils.wrap(base, attr, _wrapper(resource=mw_path))

        # Instrument class-based middleware
        elif isclass(mw):
            for hook in [
                "process_request",
                "process_response",
                "process_view",
                "process_template_response",
                "__call__",
            ]:
                if hasattr(mw, hook) and not trace_utils.iswrapped(mw, hook):
                    trace_utils.wrap(
                        mw, hook, traced_func(django, "django.middleware", resource=mw_path + ".{0}".format(hook))
                    )
            # Do a little extra for `process_exception`
            if hasattr(mw, "process_exception") and not trace_utils.iswrapped(mw, "process_exception"):
                res = mw_path + ".{0}".format("process_exception")
                trace_utils.wrap(
                    mw, "process_exception", traced_process_exception(django, "django.middleware", resource=res)
                )

    return func(*args, **kwargs)


def _set_block_tags(request, request_headers, span):
    from . import utils

    try:
        span.set_tag_str(http.STATUS_CODE, "403")
        span.set_tag_str(http.METHOD, request.method)
        url = utils.get_request_uri(request)
        query = request.META.get("QUERY_STRING", "")
        _set_url_tag(config.django, span, url, query)
        if query and config.django.trace_query_string:
            span.set_tag_str(http.QUERY_STRING, query)
        user_agent = _get_request_header_user_agent(request_headers)
        if user_agent:
            span.set_tag_str(http.USER_AGENT, user_agent)
    except Exception as e:
        log.warning("Could not set some span tags on blocked request: %s", str(e))  # noqa: G200


def _block_request_callable(request, request_headers, span):
    # This is used by user-id blocking to block responses. It could be called
    # at any point so it's a callable stored in the ASM context.
    from django.core.exceptions import PermissionDenied

    core.set_item(WAF_CONTEXT_NAMES.BLOCKED, _asm_constants.WAF_ACTIONS.DEFAULT_PARAMETERS, span=span)
    _set_block_tags(request, request_headers, span)
    raise PermissionDenied()


@trace_utils.with_traced_module
def traced_get_response(django, pin, func, instance, args, kwargs):
    """Trace django.core.handlers.base.BaseHandler.get_response() (or other implementations).

    This is the main entry point for requests.

    Django requests are handled by a Handler.get_response method (inherited from base.BaseHandler).
    This method invokes the middleware chain and returns the response generated by the chain.
    """
    from ddtrace.contrib.django.compat import get_resolver

    from . import utils

    request = get_argument_value(args, kwargs, 0, "request")
    if request is None:
        return func(*args, **kwargs)

    trace_utils.activate_distributed_headers(pin.tracer, int_config=config.django, request_headers=request.META)
    request_headers = utils._get_request_headers(request)

    with _asm_request_context.asm_request_context_manager(
        request.META.get("REMOTE_ADDR"),
        request_headers,
        headers_case_sensitive=django.VERSION < (2, 2),
    ):
        with pin.tracer.trace(
            schematize_url_operation("django.request", protocol="http", direction=SpanDirection.INBOUND),
            resource=utils.REQUEST_DEFAULT_RESOURCE,
            service=trace_utils.int_service(pin, config.django),
            span_type=SpanTypes.WEB,
        ) as span:
            _asm_request_context.set_block_request_callable(
                functools.partial(_block_request_callable, request, request_headers, span)
            )
            span.set_tag_str(COMPONENT, config.django.integration_name)

            # set span.kind to the type of request being performed
            span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

            utils._before_request_tags(pin, span, request)
            span._metrics[SPAN_MEASURED_KEY] = 1

            response = None

            def blocked_response():
                from django.http import HttpResponse

                block_config = core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span)
                desired_type = block_config.get("type", "auto")
                status = block_config.get("status_code", 403)
                if desired_type == "none":
                    response = HttpResponse("", status=status)
                    location = block_config.get("location", "")
                    if location:
                        response["location"] = location
                else:
                    if desired_type == "auto":
                        ctype = "text/html" if "text/html" in request_headers.get("Accept", "").lower() else "text/json"
                    else:
                        ctype = "text/" + desired_type
                    content = appsec_utils._get_blocked_template(ctype)
                    response = HttpResponse(content, content_type=ctype, status=status)
                    response.content = content
                utils._after_request_tags(pin, span, request, response)
                return response

            try:
                if config._appsec_enabled:
                    # [IP Blocking]
                    if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
                        response = blocked_response()
                        return response

                    # set context information for [Suspicious Request Blocking]
                    query = request.META.get("QUERY_STRING", "")
                    uri = utils.get_request_uri(request)
                    if uri is not None and query:
                        uri += "?" + query
                    resolver = get_resolver(getattr(request, "urlconf", None))
                    if resolver:
                        try:
                            path = resolver.resolve(request.path_info).kwargs
                            log.debug("resolver.pattern %s", path)
                        except Exception:
                            path = None
                    parsed_query = request.GET
                    body = utils._extract_body(request)
                    trace_utils.set_http_meta(
                        span,
                        config.django,
                        method=request.method,
                        query=query,
                        raw_uri=uri,
                        request_path_params=path,
                        parsed_query=parsed_query,
                        request_body=body,
                        request_cookies=request.COOKIES,
                    )
                    log.debug("Django WAF call for Suspicious Request Blocking on request")
                    _asm_request_context.call_waf_callback()
                    # [Suspicious Request Blocking on request]
                    if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
                        response = blocked_response()
                        return response
                response = func(*args, **kwargs)
                if config._appsec_enabled:
                    # [Blocking by client code]
                    if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
                        response = blocked_response()
                        return response
                return response
            finally:
                # DEV: Always set these tags, this is where `span.resource` is set
                utils._after_request_tags(pin, span, request, response)
                if config._appsec_enbled and config._api_security_enabled:
                    trace_utils.set_http_meta(span, config.django, route=span.get_tag("http.route"))
                # if not blocked yet, try blocking rules on response
                if config._appsec_enabled and not core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
                    log.debug("Django WAF call for Suspicious Request Blocking on response")
                    _asm_request_context.call_waf_callback()
                    # [Suspicious Request Blocking on response]
                    if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
                        response = blocked_response()
                        return response  # noqa: B012


@trace_utils.with_traced_module
def traced_template_render(django, pin, wrapped, instance, args, kwargs):
    """Instrument django.template.base.Template.render for tracing template rendering."""
    # DEV: Check here in case this setting is configured after a template has been instrumented
    if not config.django.instrument_templates:
        return wrapped(*args, **kwargs)

    template_name = maybe_stringify(getattr(instance, "name", None))
    if template_name:
        resource = template_name
    else:
        resource = "{0}.{1}".format(func_name(instance), wrapped.__name__)

    with pin.tracer.trace("django.template.render", resource=resource, span_type=http.TEMPLATE) as span:
        span.set_tag_str(COMPONENT, config.django.integration_name)

        if template_name:
            span.set_tag_str("django.template.name", template_name)
        engine = getattr(instance, "engine", None)
        if engine:
            span.set_tag_str("django.template.engine.class", func_name(engine))

        return wrapped(*args, **kwargs)


def instrument_view(django, view):
    """
    Helper to wrap Django views.

    We want to wrap all lifecycle/http method functions for every class in the MRO for this view
    """
    if hasattr(view, "__mro__"):
        for cls in reversed(getmro(view)):
            _instrument_view(django, cls)

    return _instrument_view(django, view)


def _instrument_view(django, view):
    """Helper to wrap Django views."""
    from . import utils

    # All views should be callable, double check before doing anything
    if not callable(view):
        return view

    # Patch view HTTP methods and lifecycle methods
    http_method_names = getattr(view, "http_method_names", ("get", "delete", "post", "options", "head"))
    lifecycle_methods = ("setup", "dispatch", "http_method_not_allowed")
    for name in list(http_method_names) + list(lifecycle_methods):
        try:
            func = getattr(view, name, None)
            if not func or isinstance(func, wrapt.ObjectProxy):
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
                if not func or isinstance(func, wrapt.ObjectProxy):
                    continue

                resource = "{0}.{1}".format(func_name(response_cls), name)
                op_name = "django.response.{0}".format(name)
                trace_utils.wrap(response_cls, name, traced_func(django, name=op_name, resource=resource))
            except Exception:
                log.debug("Failed to instrument Django response %r function %s", response_cls, name, exc_info=True)

    # If the view itself is not wrapped, wrap it
    if not isinstance(view, wrapt.ObjectProxy):
        view = utils.DjangoViewProxy(
            view, traced_func(django, "django.view", resource=func_name(view), ignored_excs=[django.http.Http404])
        )
    return view


@trace_utils.with_traced_module
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
def traced_get_asgi_application(django, pin, func, instance, args, kwargs):
    from ddtrace.contrib.asgi import TraceMiddleware

    def django_asgi_modifier(span, scope):
        span.name = schematize_url_operation("django.request", protocol="http", direction=SpanDirection.INBOUND)

    return TraceMiddleware(func(*args, **kwargs), integration_config=config.django, span_modifier=django_asgi_modifier)


_POSSIBLE_USER_ID_FIELDS = ["pk", "id", "uid", "userid", "user_id", "PK", "ID", "UID", "USERID"]
_POSSIBLE_LOGIN_FIELDS = ["username", "user", "login", "USERNAME", "USER", "LOGIN"]
_POSSIBLE_EMAIL_FIELDS = ["email", "mail", "address", "EMAIL", "MAIL", "ADDRESS"]
_POSSIBLE_NAME_FIELDS = ["name", "fullname", "full_name", "NAME", "FULLNAME", "FULL_NAME"]


def _find_in_user_model(user, possible_fields):
    for field in possible_fields:
        value = getattr(user, field, None)
        if value:
            return value

    return None  # explicit to make clear it has a meaning


def _get_userid(user):
    user_login = getattr(user, config._user_model_login_field, None)
    if user_login:
        return user_login

    return _find_in_user_model(user, _POSSIBLE_USER_ID_FIELDS)


def _get_username(user):
    username = getattr(user, config._user_model_name_field, None)
    if username:
        return username

    if hasattr(user, "get_username"):
        try:
            return user.get_username()
        except Exception:
            log.debug("User model get_username member produced an exception: ", exc_info=True)

    user_type = type(user)
    if hasattr(user_type, "USERNAME_FIELD"):
        return getattr(user, user_type.USERNAME_FIELD, None)

    return _find_in_user_model(user, _POSSIBLE_LOGIN_FIELDS)


def _get_user_email(user):
    email = getattr(user, config._user_model_email_field, None)
    if email:
        return email

    user_type = type(user)
    if hasattr(user_type, "EMAIL_FIELD"):
        return getattr(user, user_type.EMAIL_FIELD, None)

    return _find_in_user_model(user, _POSSIBLE_EMAIL_FIELDS)


def _get_name(user):
    if hasattr(user, "get_full_name"):
        try:
            return user.get_full_name()
        except Exception:
            log.debug("User model get_full_name member produced an exception: ", exc_info=True)

    if hasattr(user, "first_name") and hasattr(user, "last_name"):
        return "%s %s" % (user.first_name, user.last_name)

    return getattr(user, "name", None)


def _get_user_info(user):
    """
    In safe mode, try to get the user id from the user object.
    In extended mode, try to also get the username (which will be the returned user_id),
    email and name.

    Since the Django Authentication model supports pluggable users models
    there is some heuristic involved in extracting this field, though it should
    work well for the most common case with the default User model.
    """
    user_extra_info = {}

    if config._automatic_login_events_mode == "extended":
        user_id = _get_username(user)
        if not user_id:
            user_id = _find_in_user_model(user, _POSSIBLE_USER_ID_FIELDS)

        user_extra_info = {
            "login": user_id,
            "email": _get_user_email(user),
            "name": _get_name(user),
        }
    else:  # safe mode, default
        user_id = _get_userid(user)

    if not user_id:
        return None, {}

    return user_id, user_extra_info


@trace_utils.with_traced_module
def traced_login(django, pin, func, instance, args, kwargs):
    func(*args, **kwargs)

    try:
        mode = config._automatic_login_events_mode
        request = get_argument_value(args, kwargs, 0, "request")
        user = get_argument_value(args, kwargs, 1, "user")

        if not config._appsec_enabled or mode == "disabled":
            return

        if user and str(user) != "AnonymousUser":
            user_id, user_extra = _get_user_info(user)
            if not user_id:
                log.debug(
                    "Automatic Login Events Tracking: " "Could not determine user id field user for the %s user Model",
                    type(user),
                )
                return

            with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
                from ddtrace.contrib.django.compat import user_is_authenticated

                if user_is_authenticated(user):
                    session_key = getattr(request, "session_key", None)
                    track_user_login_success_event(
                        pin.tracer,
                        user_id=user_id,
                        session_id=session_key,
                        propagate=True,
                        login_events_mode=mode,
                        **user_extra
                    )
                    return
                else:
                    # Login failed but the user exists
                    track_user_login_failure_event(pin.tracer, user_id=user_id, exists=True, login_events_mode=mode)
                    return
        else:
            # Login failed and the user is unknown
            if user:
                if mode == "extended":
                    user_id = _get_username(user)
                else:  # safe mode
                    user_id = _find_in_user_model(user, _POSSIBLE_USER_ID_FIELDS)
                if not user_id:
                    user_id = "AnonymousUser"

                track_user_login_failure_event(pin.tracer, user_id=user_id, exists=False, login_events_mode=mode)
                return
    except Exception:
        log.debug("Error while trying to trace Django login", exc_info=True)


@trace_utils.with_traced_module
def traced_authenticate(django, pin, func, instance, args, kwargs):
    result_user = func(*args, **kwargs)
    try:
        mode = config._automatic_login_events_mode
        if not config._appsec_enabled or mode == "disabled":
            return result_user

        userid_list = _POSSIBLE_USER_ID_FIELDS if mode == "safe" else _POSSIBLE_LOGIN_FIELDS

        for possible_key in userid_list:
            if possible_key in kwargs:
                user_id = kwargs[possible_key]
                break
        else:
            user_id = "missing"

        if not result_user:
            with pin.tracer.trace("django.contrib.auth.login", span_type=SpanTypes.AUTH):
                track_user_login_failure_event(pin.tracer, user_id=user_id, exists=False, login_events_mode=mode)
    except Exception:
        log.debug("Error while trying to trace Django authenticate", exc_info=True)

    return result_user


def unwrap_views(func, instance, args, kwargs):
    """
    Django channels uses path() and re_path() to route asgi applications. This broke our initial
    assumption that
    django path/re_path/url functions only accept views. Here we unwrap ddtrace view
    instrumentation from asgi
    applications.

    Ex. ``channels.routing.URLRouter([path('', get_asgi_application())])``
    On startup ddtrace.contrib.django.path.instrument_view() will wrap get_asgi_application in a
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

    if config.django.instrument_middleware:
        when_imported("django.core.handlers.base")(
            lambda m: trace_utils.wrap(m, "BaseHandler.load_middleware", traced_load_middleware(django))
        )

    when_imported("django.core.handlers.wsgi")(lambda m: trace_utils.wrap(m, "WSGIRequest.__init__", wrap_wsgi_environ))
    core.dispatch("django.patch", [])

    @when_imported("django.core.handlers.base")
    def _(m):
        import django

        trace_utils.wrap(m, "BaseHandler.get_response", traced_get_response(django))
        if django.VERSION >= (3, 1):
            # Have to inline this import as the module contains syntax incompatible with Python 3.5 and below
            from ._asgi import traced_get_response_async

            trace_utils.wrap(m, "BaseHandler.get_response_async", traced_get_response_async(django))

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

    if config.django.instrument_templates:
        when_imported("django.template.base")(
            lambda m: trace_utils.wrap(m, "Template.render", traced_template_render(django))
        )

    if django.VERSION < (4, 0, 0):
        when_imported("django.conf.urls")(lambda m: trace_utils.wrap(m, "url", traced_urls_path(django)))

    if django.VERSION >= (2, 0, 0):

        @when_imported("django.urls")
        def _(m):
            trace_utils.wrap(m, "path", traced_urls_path(django))
            trace_utils.wrap(m, "re_path", traced_urls_path(django))

    when_imported("django.views.generic.base")(lambda m: trace_utils.wrap(m, "View.as_view", traced_as_view(django)))

    @when_imported("channels.routing")
    def _(m):
        import channels

        channels_version = tuple(int(x) for x in channels.__version__.split("."))
        if channels_version >= (3, 0):
            # ASGI3 is only supported in channels v3.0+
            trace_utils.wrap(m, "URLRouter.__init__", unwrap_views)


def wrap_wsgi_environ(wrapped, _instance, args, kwargs):
    return core.dispatch("django.wsgi_environ", wrapped, _instance, args, kwargs)[0][0]


def patch():
    import django

    if getattr(django, "_datadog_patch", False):
        return
    _patch(django)

    django._datadog_patch = True


def _unpatch(django):
    trace_utils.unwrap(django.apps.registry.Apps, "populate")
    trace_utils.unwrap(django.core.handlers.base.BaseHandler, "load_middleware")
    trace_utils.unwrap(django.core.handlers.base.BaseHandler, "get_response")
    trace_utils.unwrap(django.core.handlers.base.BaseHandler, "get_response_async")
    trace_utils.unwrap(django.template.base.Template, "render")
    trace_utils.unwrap(django.conf.urls.static, "static")
    trace_utils.unwrap(django.conf.urls, "url")
    trace_utils.unwrap(django.contrib.auth.login, "login")
    trace_utils.unwrap(django.contrib.auth.authenticate, "authenticate")
    if django.VERSION >= (2, 0, 0):
        trace_utils.unwrap(django.urls, "path")
        trace_utils.unwrap(django.urls, "re_path")
    trace_utils.unwrap(django.views.generic.base.View, "as_view")
    for conn in django.db.connections.all():
        trace_utils.unwrap(conn, "cursor")
    trace_utils.unwrap(django.db.utils.ConnectionHandler, "__getitem__")


def unpatch():
    import django

    if not getattr(django, "_datadog_patch", False):
        return

    _unpatch(django)

    django._datadog_patch = False
