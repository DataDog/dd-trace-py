from inspect import unwrap
import weakref

import flask
import werkzeug
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import NotFound

from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.packages import get_version_for_package
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import get_blocked


# Not all versions of flask/werkzeug have this mixin
try:
    from werkzeug.wrappers.json import JSONMixin

    _HAS_JSON_MIXIN = True
except ImportError:
    _HAS_JSON_MIXIN = False

# DispatcherMiddleware has shipped since werkzeug 1.0 (below our floor); guard the import for safety only.
try:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware as _DispatcherMiddleware
except ImportError:
    _DispatcherMiddleware = None  # type: ignore[assignment,misc]

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.contrib.internal.wsgi.wsgi import _DDWSGIMiddlewareBase
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.utils.version import parse_version

from .wrappers import _wrap_call_with_tracing_check
from .wrappers import simple_call_wrapper
from .wrappers import with_tracing_enabled
from .wrappers import wrap_function
from .wrappers import wrap_view


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore


log = get_logger(__name__)

FLASK_VERSION = "flask.version"
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Configure default configuration
config._add(
    "flask",
    dict(
        # Flask service configuration
        _default_service=schematize_service_name("flask"),
        collect_view_args=True,
        distributed_tracing_enabled=True,
        template_default_name="<memory>",
        trace_signals=True,
    ),
)


def get_version() -> str:
    return get_version_for_package("flask")


def _supported_versions() -> dict[str, str]:
    return {"flask": ">=1.1.4"}


def get_werkzeug_version() -> str:
    return get_version_for_package("werkzeug")


if _HAS_JSON_MIXIN:

    class RequestWithJson(werkzeug.Request, JSONMixin):
        pass

    _RequestType = RequestWithJson
else:
    _RequestType = werkzeug.Request

# Extract flask version into a tuple e.g. (0, 12, 1) or (1, 0, 2)
# DEV: This makes it so we can do `if flask_version >= (0, 12, 0):`
# DEV: Example tests:
#      (0, 10, 0) > (0, 10)
#      (0, 10, 0) >= (0, 10, 0)
#      (0, 10, 1) >= (0, 10)
#      (0, 11, 1) >= (0, 10)
#      (0, 11, 1) >= (0, 10, 2)
#      (1, 0, 0) >= (0, 10)
#      (0, 9) == (0, 9)
#      (0, 9, 0) != (0, 9)
#      (0, 8, 5) <= (0, 9)
flask_version_str = get_version()
flask_version = parse_version(flask_version_str)

werkzeug_version_str = get_werkzeug_version()
werkzeug_version = parse_version(werkzeug_version_str)


class _FlaskWSGIMiddleware(_DDWSGIMiddlewareBase):
    _request_call_name = schematize_url_operation("flask.request", protocol="http", direction=SpanDirection.INBOUND)
    _application_call_name = "flask.application"
    _response_call_name = "flask.response"

    def _wrapped_start_response(self, start_response, ctx, status_code, headers, exc_info=None):
        core.dispatch("flask.start_response.pre", (flask.request, ctx, config.flask, status_code, headers))
        if not get_blocked():
            core.dispatch("flask.start_response", ("Flask",))
            if block_config := get_blocked():
                # response code must be set here, or it will be too late
                result_content = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                    "flask.block.request.content", ()
                ).block_requested
                if result_content:
                    _, status, response_headers = result_content.value
                    result = start_response(str(status), response_headers)
                else:
                    response_headers = (
                        [] if block_config.type == "none" else [("content-type", block_config.content_type)]
                    )
                    result = start_response(str(block_config.status_code), response_headers)
                core.dispatch(
                    "flask.start_response.blocked", (ctx, config.flask, response_headers, block_config.status_code)
                )
            else:
                result = start_response(status_code, headers)
        else:
            result = start_response(status_code, headers)
        return result

    def _request_call_modifier(self, ctx, parsed_headers=None):
        environ = ctx.get_item("environ")
        # Create a werkzeug request from the `environ` to make interacting with it easier
        # DEV: This executes before a request context is created
        request = _RequestType(environ)

        req_body = None
        result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
            "flask.request_call_modifier",
            (
                ctx,
                config.flask,
                request,
                environ,
                _HAS_JSON_MIXIN,
                FLASK_VERSION,
                flask_version_str,
                BadRequest,
            ),
        ).request_body
        if result:
            req_body = result.value
        core.dispatch("flask.request_call_modifier.post", (ctx, config.flask, request, req_body))


def patch():
    """
    Patch `flask` module for tracing
    """
    # Check to see if we have patched Flask yet or not
    if getattr(flask, "_datadog_patch", False):
        return
    flask._datadog_patch = True

    core.dispatch("flask.patch", (flask_version,))
    # flask.app.Flask methods that have custom tracing (add metadata, wrap functions, etc)
    _w("flask", "Flask.wsgi_app", patched_wsgi_app)
    _w("flask", "Flask.dispatch_request", request_patcher("dispatch_request"))
    _w("flask", "Flask.preprocess_request", request_patcher("preprocess_request"))
    _w("flask", "Flask.add_url_rule", patched_add_url_rule)
    _w("flask", "Flask.endpoint", patched_endpoint)

    # Walk DispatcherMiddleware at construction so mounted sub-apps are discovered before any traffic.
    try:
        _w("werkzeug.middleware.dispatcher", "DispatcherMiddleware.__init__", patched_dispatcher_middleware_init)
    except (ImportError, AttributeError):
        log.debug("Failed to patch werkzeug DispatcherMiddleware.__init__ for endpoint discovery")

    _w("flask", "Flask.finalize_request", patched_finalize_request)

    if flask_version >= (2, 0, 0):
        _w("flask", "Flask.register_error_handler", patched_register_error_handler)
    else:
        _w("flask", "Flask._register_error_handler", patched__register_error_handler)

    flask_hooks = [
        "before_request",
        "after_request",
        "teardown_request",
        "teardown_appcontext",
    ]
    if flask_version < (2, 3, 0):
        flask_hooks.append("before_first_request")

    for hook in flask_hooks:
        _w("flask", "Flask.{}".format(hook), patched_flask_hook)
    _w("flask", "after_this_request", patched_flask_hook)

    flask_app_traces = [
        "process_response",
        "handle_exception",
        "handle_http_exception",
        "handle_user_exception",
        "do_teardown_request",
        "do_teardown_appcontext",
        "send_static_file",
    ]
    if flask_version < (2, 2, 0):
        flask_app_traces.append("try_trigger_before_first_request_functions")

    for name in flask_app_traces:
        _w("flask", "Flask.{}".format(name), simple_call_wrapper("flask.{}".format(name)))
    # flask static file helpers
    _w("flask", "send_file", simple_call_wrapper("flask.send_file"))

    # flask.json.jsonify
    _w("flask", "jsonify", patched_jsonify)

    _w("flask.templating", "_render", patched_render)
    _w("flask", "render_template", _build_render_template_wrapper("render_template"))
    _w("flask", "render_template_string", _build_render_template_wrapper("render_template_string"))
    if werkzeug_version >= (2, 1, 0):
        try:
            _w("werkzeug.debug.tbtools", "DebugTraceback.render_debugger_html", patched_render_debugger_html)
        except AttributeError:
            log.debug("Failed to patch DebugTraceback.render_debugger_html, not supported by this werkzeug version")
    else:
        try:
            _w("werkzeug.debug.tbtools", "Traceback.render_summary", patched_render_debugger_html)
        except AttributeError:
            log.debug("Failed to patch Traceback.render_summary, not supported by this werkzeug version")

    bp_hooks = [
        "after_app_request",
        "after_request",
        "before_app_request",
        "before_request",
        "teardown_request",
        "teardown_app_request",
    ]
    if flask_version < (2, 3, 0):
        bp_hooks.append("before_app_first_request")

    for hook in bp_hooks:
        _w("flask", "Blueprint.{}".format(hook), patched_flask_hook)

    if config.flask["trace_signals"]:
        signals = [
            "template_rendered",
            "request_started",
            "request_finished",
            "request_tearing_down",
            "got_request_exception",
            "appcontext_tearing_down",
        ]
        # These were added in 0.11.0
        if flask_version >= (0, 11):
            signals.append("before_render_template")

        # These were added in 0.10.0
        if flask_version >= (0, 10):
            signals.append("appcontext_pushed")
            signals.append("appcontext_popped")
            signals.append("message_flashed")

        for signal in signals:
            module = "flask"

            # v0.9 missed importing `appcontext_tearing_down` in `flask/__init__.py`
            #  https://github.com/pallets/flask/blob/0.9/flask/__init__.py#L35-L37
            #  https://github.com/pallets/flask/blob/0.9/flask/signals.py#L52
            # DEV: Version 0.9 doesn't have a patch version
            if flask_version <= (0, 9) and signal == "appcontext_tearing_down":
                module = "flask.signals"

            # DEV: Patch `receivers_for` instead of `connect` to ensure we don't mess with `disconnect`
            _w(module, "{}.receivers_for".format(signal), patched_signal_receivers_for(signal))


def unpatch():
    if not getattr(flask, "_datadog_patch", False):
        return
    flask._datadog_patch = False

    props = [
        # Flask
        "Flask.wsgi_app",
        "Flask.dispatch_request",
        "Flask.add_url_rule",
        "Flask.endpoint",
        "Flask.preprocess_request",
        "Flask.process_response",
        "Flask.handle_exception",
        "Flask.handle_http_exception",
        "Flask.handle_user_exception",
        "Flask.do_teardown_request",
        "Flask.do_teardown_appcontext",
        "Flask.send_static_file",
        # Flask Hooks
        "Flask.before_request",
        "Flask.after_request",
        "Flask.teardown_request",
        "Flask.teardown_appcontext",
        # Blueprint Hooks
        "Blueprint.after_app_request",
        "Blueprint.after_request",
        "Blueprint.before_app_request",
        "Blueprint.before_request",
        "Blueprint.teardown_request",
        "Blueprint.teardown_app_request",
        # Signals
        "template_rendered.receivers_for",
        "request_started.receivers_for",
        "request_finished.receivers_for",
        "request_tearing_down.receivers_for",
        "got_request_exception.receivers_for",
        "appcontext_tearing_down.receivers_for",
        # Top level props
        "after_this_request",
        "send_file",
        "jsonify",
        "render_template",
        "render_template_string",
        "templating._render",
    ]

    props.append("Flask.finalize_request")

    if flask_version >= (2, 0, 0):
        props.append("Flask.register_error_handler")
    else:
        props.append("Flask._register_error_handler")

    # These were added in 0.11.0
    if flask_version >= (0, 11):
        props.append("before_render_template.receivers_for")

    # These were added in 0.10.0
    if flask_version >= (0, 10):
        props.append("appcontext_pushed.receivers_for")
        props.append("appcontext_popped.receivers_for")
        props.append("message_flashed.receivers_for")

    # These were removed in 2.2.0
    if flask_version < (2, 2, 0):
        props.append("Flask.try_trigger_before_first_request_functions")

    # These were removed in 2.3.0
    if flask_version < (2, 3, 0):
        props.append("Flask.before_first_request")
        props.append("Blueprint.before_app_first_request")

    try:
        import werkzeug.middleware.dispatcher as _wmd

        _u(_wmd.DispatcherMiddleware, "__init__")
    except (ImportError, AttributeError):
        pass

    for prop in props:
        # Handle 'flask.request_started.receivers_for'
        obj = flask

        # v0.9.0 missed importing `appcontext_tearing_down` in `flask/__init__.py`
        #  https://github.com/pallets/flask/blob/0.9/flask/__init__.py#L35-L37
        #  https://github.com/pallets/flask/blob/0.9/flask/signals.py#L52
        # DEV: Version 0.9 doesn't have a patch version
        if flask_version <= (0, 9) and prop == "appcontext_tearing_down.receivers_for":
            obj = flask.signals

        if "." in prop:
            attr, _, prop = prop.partition(".")
            obj = getattr(obj, attr, object())
        _u(obj, prop)


def patched_wsgi_app(wrapped, instance, args, kwargs):
    # Starting point for all requests. PEP-3333 args. Endpoint discovery runs unconditionally —
    # gated by ``asm_config._api_security_endpoint_collection`` inside ``_collect_routes_once``,
    # not by tracing — so the pre-PR contract that registration is tracing-independent is preserved.
    environ, start_response = args
    _collect_routes_once(instance, environ.get("SCRIPT_NAME") or "")
    if not is_tracing_enabled():
        return wrapped(*args, **kwargs)
    middleware = _FlaskWSGIMiddleware(wrapped, None, config.flask)
    return middleware(environ, start_response)


# WeakKeyDictionary[Flask, set[script_name]] — perf gate for ``_collect_routes_once``.
# Entries drop when Flask releases an app; ``endpoint_collection`` dedupes by (method, path).
_collected_scripts_by_app: "weakref.WeakKeyDictionary[flask.Flask, set[str]]" = weakref.WeakKeyDictionary()


def _collect_flask_routes(app, script_name):
    """Register every rule in ``app.url_map`` into endpoint_collection, prefixed by ``script_name``.

    Flask's url_map already contains Blueprint-prefixed rules; ``script_name`` adds the
    DispatcherMiddleware mount prefix that Flask itself is unaware of.
    """
    try:
        url_map = app.url_map
    except Exception:
        return
    # Strip trailing ``/`` from the prefix — rule.rule always starts with ``/`` (Werkzeug invariant).
    prefix = script_name.rstrip("/") if script_name else ""
    for rule in url_map.iter_rules():
        try:
            full_path = prefix + rule.rule
            # Register every method the framework actually serves: user-declared + Werkzeug-auto-HEAD
            # + Flask-auto-OPTIONS. Anything that yields 2xx/4xx (not 405) is part of the attack surface.
            # ``rule.methods is None`` (raw Werkzeug add() without Flask) means the route responds to
            # every method — register the ``*`` wildcard convention.
            methods: list[str] = ["*"] if rule.methods is None else list(rule.methods)
            for method in methods:
                endpoint_collection.add_endpoint(method, full_path, operation_name="flask.request")
        except Exception:
            log.debug("Failed to register Flask rule %r for endpoint collection", rule, exc_info=True)


def _walk_wsgi_mounts(wsgi_app, script_name):
    """Yield ``(flask_app, full_prefix)`` for every Flask app reachable from a WSGI
    chain, recursing through ``werkzeug.middleware.dispatcher.DispatcherMiddleware``
    and composing mount prefixes as it descends.
    """
    if _DispatcherMiddleware is not None and isinstance(wsgi_app, _DispatcherMiddleware):
        # Default app first (handles any path that doesn't match a mount), then each mount.
        # Strip trailing ``/`` so ``/outer/`` + ``/inner`` composes as ``/outer/inner``.
        yield from _walk_wsgi_mounts(wsgi_app.app, script_name)
        parent = script_name.rstrip("/") if script_name else ""
        for mount_prefix, mounted in wsgi_app.mounts.items():
            yield from _walk_wsgi_mounts(mounted, parent + mount_prefix)
        return
    # ``app.wsgi_app`` is a bound method (``__self__`` is the Flask app); a direct Flask instance also matches.
    target = getattr(wsgi_app, "__self__", wsgi_app)
    if isinstance(target, flask.Flask):
        yield target, script_name


def _collect_routes_once(app, script_name=""):
    """Walk ``app`` (and any DispatcherMiddleware reachable from its wsgi_app) once per ``(app, script_name)``.

    The cached entry is invalidated by ``patched_add_url_rule`` on every ``Flask.add_url_rule`` call, so an
    app factory that constructs a ``DispatcherMiddleware`` before registering its route modules still gets a
    fresh walk on the next request. Steady-state hot paths keep using the cached walk.
    """
    if not asm_config._api_security_endpoint_collection:
        return
    if not isinstance(app, flask.Flask):
        return
    scripts = _collected_scripts_by_app.get(app)
    if scripts is None:
        scripts = set()
        try:
            _collected_scripts_by_app[app] = scripts
        except TypeError:
            # Unhashable Flask subclass — guard so it doesn't crash the request path.
            return
    if script_name in scripts:
        return
    scripts.add(script_name)
    try:
        _collect_flask_routes(app, script_name)
        # Pattern A: user assigned a DM back onto app.wsgi_app — descend it to register mounts on first request.
        for sub_app, sub_prefix in _walk_wsgi_mounts(app.wsgi_app, script_name):
            if sub_app is app:
                continue
            _collect_routes_once(sub_app, sub_prefix)
    except Exception:
        log.debug("Failed to collect Flask routes for endpoint collection", exc_info=True)


def patched_dispatcher_middleware_init(wrapped, instance, args, kwargs):
    """Walk mounts eagerly at DispatcherMiddleware construction so Pattern B (DM as the outer WSGI app, not
    owned by any Flask app) discovers sub-apps that never receive traffic. Pattern A is handled by the
    first-request walk in ``patched_wsgi_app``.
    """
    wrapped(*args, **kwargs)
    # Bail when endpoint collection is disabled — DM construction is on the synchronous app-startup path.
    if not asm_config._api_security_endpoint_collection:
        return
    try:
        for sub_app, sub_prefix in _walk_wsgi_mounts(getattr(instance, "app", None), ""):
            _collect_routes_once(sub_app, sub_prefix)
        for mount_prefix, mounted in (getattr(instance, "mounts", None) or {}).items():
            for sub_app, sub_prefix in _walk_wsgi_mounts(mounted, mount_prefix):
                _collect_routes_once(sub_app, sub_prefix)
    except Exception:
        log.debug("Failed to walk DispatcherMiddleware mounts for endpoint collection", exc_info=True)


def patched_finalize_request(wrapped, instance, args, kwargs):
    """
    Wrapper for flask.app.Flask.finalize_request
    """
    rv = wrapped(*args, **kwargs)
    if getattr(rv, "is_sequence", False):
        core.dispatch("flask.finalize_request.post", (rv.response, rv.headers))
    return rv


def patched_render_debugger_html(wrapped, instance, args, kwargs):
    res = wrapped(*args, **kwargs)
    core.dispatch("werkzeug.render_debugger_html", (res,))
    return res


def patched_add_url_rule(wrapped, instance, args, kwargs):
    """Wrap all views attached to this app and re-register endpoints under every known SCRIPT_NAME.

    Endpoint registration is normally deferred to ``_collect_routes_once()`` (the mount prefix isn't known
    here, since sub-apps are built before being mounted). But when an app factory mounts a
    ``DispatcherMiddleware`` *before* registering its route modules, the eager DM walk runs against an empty
    ``url_map`` and the cache says "already walked" — so without a re-walk here, late-registered routes for
    sub-apps that never receive traffic would never reach endpoint discovery. Re-running the walk under each
    cached SCRIPT_NAME after the wrapped ``add_url_rule`` picks up the new rule immediately and is idempotent
    (``endpoint_collection`` dedupes by ``(method, path)``).
    """

    def _wrap(rule, endpoint=None, view_func=None, provide_automatic_options=None, **kwargs):
        wrapped_view = None
        if view_func is not None:
            # TODO: `if hasattr(view_func, 'view_class')` then this was generated from a `flask.views.View`
            #   should we do something special with these views? Change the name/resource? Add tags?
            core.dispatch("service_entrypoint.patch", (unwrap(view_func),))
            wrapped_view = wrap_view(instance, view_func, name=endpoint, resource=rule)
        result = wrapped(
            rule,
            endpoint=endpoint,
            view_func=wrapped_view,
            provide_automatic_options=provide_automatic_options,
            **kwargs,
        )
        # Re-walk on the registration path (never the request hot path) so the new rule reaches
        # endpoint_collection even when no request ever hits the app it was added to.
        if asm_config._api_security_endpoint_collection:
            try:
                scripts = _collected_scripts_by_app.get(instance)
            except TypeError:
                scripts = None  # non-hashable Flask subclass — guard mirrors the WeakKeyDictionary insertion
            if scripts:
                for script_name in list(scripts):
                    _collect_flask_routes(instance, script_name)
        return result

    return _wrap(*args, **kwargs)


def patched_endpoint(wrapped, instance, args, kwargs):
    """Wrapper for flask.app.Flask.endpoint to ensure all endpoints are wrapped"""
    endpoint = kwargs.get("endpoint", args[0])

    def _wrapper(func):
        core.dispatch("service_entrypoint.patch", (unwrap(func),))
        return wrapped(endpoint)(wrap_function(instance, func, resource=endpoint))

    return _wrapper


def patched_flask_hook(wrapped, instance, args, kwargs):
    func = get_argument_value(args, kwargs, 0, "f")
    return wrapped(wrap_function(instance, func))


def traced_render_template(wrapped, instance, args, kwargs):
    return _build_render_template_wrapper("render_template")(wrapped, instance, args, kwargs)


def traced_render_template_string(wrapped, instance, args, kwargs):
    return _build_render_template_wrapper("render_template_string")(wrapped, instance, args, kwargs)


def _build_render_template_wrapper(name):
    name = "flask.%s" % name

    def traced_render(wrapped, instance, args, kwargs):
        if not is_tracing_enabled():
            return wrapped(*args, **kwargs)
        with (
            core.context_with_data(
                "flask.render_template",
                span_name=name,
                flask_config=config.flask,
                tags={COMPONENT: config.flask.integration_name},
                span_type=SpanTypes.TEMPLATE,
                integration_config=config.flask,
            ) as ctx,
            ctx.span,
        ):
            return wrapped(*args, **kwargs)

    return traced_render


def patched_render(wrapped, instance, args, kwargs):
    if not is_tracing_enabled():
        return wrapped(*args, **kwargs)

    template_position = 1 if flask_version >= (2, 2, 0) else 0
    try:
        template = get_argument_value(args, kwargs, template_position, "template")
    except ArgumentError:
        template = None

    core.dispatch("flask.render", (template, config.flask))
    return wrapped(*args, **kwargs)


def patched__register_error_handler(wrapped, instance, args, kwargs):
    def _wrap(key, code_or_exception, f):
        return wrapped(key, code_or_exception, wrap_function(instance, f))

    return _wrap(*args, **kwargs)


def patched_register_error_handler(wrapped, instance, args, kwargs):
    def _wrap(code_or_exception, f):
        return wrapped(code_or_exception, wrap_function(instance, f))

    return _wrap(*args, **kwargs)


def request_patcher(name):
    @with_tracing_enabled
    def _patched_request(wrapped, instance, args, kwargs):
        with (
            core.context_with_data(
                "flask._patched_request",
                span_name=".".join(("flask", name)),
                service=trace_utils.int_service(None, config.flask),
                flask_config=config.flask,
                flask_request=flask.request,
                ignored_exception_type=NotFound,
                tags={COMPONENT: config.flask.integration_name},
            ) as ctx,
            ctx.span,
        ):
            core.dispatch("flask._patched_request", (ctx,))
            return wrapped(*args, **kwargs)

    return _patched_request


def patched_signal_receivers_for(signal):
    def outer(wrapped, instance, args, kwargs):
        sender = get_argument_value(args, kwargs, 0, "sender")
        # See if they gave us the flask.app.Flask as the sender
        app = None
        if isinstance(sender, flask.Flask):
            app = sender
        for receiver in wrapped(*args, **kwargs):
            yield _wrap_call_with_tracing_check(receiver, app, func_name(receiver), signal=signal)

    return outer


def patched_jsonify(wrapped, instance, args, kwargs):
    if not is_tracing_enabled():
        return wrapped(*args, **kwargs)

    with (
        core.context_with_data(
            "flask.jsonify",
            span_name="flask.jsonify",
            flask_config=config.flask,
            tags={COMPONENT: config.flask.integration_name},
        ) as ctx,
        ctx.span,
    ):
        return wrapped(*args, **kwargs)
