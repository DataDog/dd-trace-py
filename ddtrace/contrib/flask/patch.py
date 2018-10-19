"""
TODO:

Document:
  - which Flask functions/methods we override
  - which span name/resources they map to
  - metadata captured by each
  - which can be pinned
  - configuration options
  - check compatibility with versions and track differences between them

Write tests:
  - ugh

Clean up and contribute test app to trace-eamples

Make sure we are off of master and not 0.16-dev
"""
import flask
import os
import werkzeug
from wrapt import wrap_function_wrapper as _w

from ddtrace import config, Pin

from ...ext import AppTypes
from ...ext import http
from ...propagation.http import HTTPPropagator
from ...utils.wrappers import unwrap as _u
from .helpers import get_current_app, get_current_span, get_inherited_pin, func_name, simple_tracer, with_instance_pin
from .wrappers import wrap_function, wrap_signal


# Configure default configuration
config._add('flask', dict(
    # Flask service configuration
    # DEV: Environment variable 'DATADOG_SERVICE_NAME' used for backwards compatibility
    service_name=os.environ.get('DATADOG_SERVICE_NAME') or 'flask',
    app='flask',
    app_type=AppTypes.web,

    collect_view_args=True,
    distributed_tracing_enabled=False,
    template_default_name='<memory>',
    trace_signals=True,
    error_codes=set([500, ]),
))


# Extract flask version into a tuple e.g. (0, 12, 1) or (1, 0, 2)
# DEV: This makes it so we can do `if flask_version >= (0, 12, 0):`
flask_version_str = getattr(flask, '__version__', '0.0.0')
flask_version = tuple([int(i) for i in flask_version_str.split('.')])


def patch():
    """
    Patch `flask` module for tracing
    """
    # Check to see if we have patched Flask yet or not
    if getattr(flask, '_datadog_patch', False):
        return
    setattr(flask, '_datadog_patch', True)

    # Attach service pin to `flask.app.Flask`
    Pin(
        service=config.flask['service_name'],
        app=config.flask['app'],
        app_type=config.flask['app_type'],
    ).onto(flask.Flask)

    # flask.app.Flask methods that have custom tracing (add metadata, wrap functions, etc)
    _w('flask', 'Flask.wsgi_app', traced_wsgi_app)
    _w('flask', 'Flask.dispatch_request', traced_dispatch_request)
    _w('flask', 'Flask.add_url_rule', traced_add_url_rule)
    _w('flask', 'Flask.endpoint', traced_endpoint)
    _w('flask', 'Flask._register_error_handler', traced_register_error_handler)

    if flask_version >= (0, 12, 0):
        _w('flask', 'Flask.finalize_request', traced_finalize_request)

    # flask.blueprints.Blueprint methods that have custom tracing (add metadata, wrap functions, etc)
    _w('flask', 'Blueprint.register', traced_blueprint_register)
    _w('flask', 'Blueprint.add_url_rule', traced_blueprint_add_url_rule)

    # flask.app.Flask traced hook decorators
    flask_hooks = [
        'before_request',
        'before_first_request',
        'after_request',
        'teardown_request',
        'teardown_appcontext',
    ]
    for hook in flask_hooks:
        _w('flask', 'Flask.{}'.format(hook), traced_flask_hook)
    _w('flask', 'after_this_request', traced_flask_hook)

    # flask.app.Flask traced methods
    flask_app_traces = [
        'preprocess_request',
        'process_response',
        'handle_exception',
        'handle_http_exception',
        'handle_user_exception',
        'try_trigger_before_first_request_functions',
        'do_teardown_request',
        'do_teardown_appcontext',
        'send_static_file',
    ]
    for name in flask_app_traces:
        _w('flask', 'Flask.{}'.format(name), simple_tracer('flask.{}'.format(name)))

    # flask static file helpers
    _w('flask', 'send_file', simple_tracer('flask.send_file'))
    _w('flask', 'send_from_directory', simple_tracer('flask.send_from_directory'))

    # flask.json.jsonify
    _w('flask', 'jsonify', traced_jsonify)

    # flask.templating traced functions
    _w('flask.templating', '_render', traced_render)
    _w('flask', 'render_template', traced_render_template)
    _w('flask', 'render_template_string', traced_render_template_string)

    # flask.blueprints.Blueprint traced hook decorators
    bp_hooks = [
        'after_app_request',
        'after_request',
        'before_app_first_request',
        'before_app_request',
        'before_request',
        'teardown_request',
        'teardown_app_request',
    ]
    for hook in bp_hooks:
        _w('flask', 'Blueprint.{}'.format(hook), traced_flask_hook)

    # flask.signals signals
    if config.flask['trace_signals']:
        signals = [
            'template_rendered',
            'request_started',
            'request_finished',
            'request_tearing_down',
            'got_request_exception',
            'appcontext_tearing_down',
            'appcontext_pushed',
            'appcontext_popped',
            'message_flashed',
        ]
        if flask_version >= (0, 11, 0):
            signals.append('before_render_template')

        for signal in signals:
            # DEV: Patch `receivers_for` instead of `connect` to ensure we don't mess with `disconnect`
            _w('flask', '{}.receivers_for'.format(signal), traced_signal_receivers_for(signal))


def unpatch():
    if not getattr(flask, '_datadog_patch', False):
        return
    setattr(flask, '_datadog_patch', False)

    props = [
        # Flask
        'Flask.wsgi_app',
        'Flask.dispatch_request',
        'Flask.add_url_rule',
        'Flask.endpoint',
        'Flask._register_error_handler',
        'Flask.finalize_request',

        'Flask.preprocess_request',
        'Flask.process_response',
        'Flask.handle_exception',
        'Flask.handle_http_exception',
        'Flask.handle_user_exception',
        'Flask.try_trigger_before_first_request_functions',
        'Flask.do_teardown_request',
        'Flask.do_teardown_appcontext',
        'Flask.send_static_file',

        # Flask Hooks
        'Flask.before_request',
        'Flask.before_first_request',
        'Flask.after_request',
        'Flask.teardown_request',
        'Flask.teardown_appcontext',

        # Blueprint
        'Blueprint.register',
        'Blueprint.add_url_rule',

        # Blueprint Hooks
        'Blueprint.after_app_request',
        'Blueprint.after_request',
        'Blueprint.before_app_first_request',
        'Blueprint.before_app_request',
        'Blueprint.before_request',
        'Blueprint.teardown_request',
        'Blueprint.teardown_app_request',

        # Signals
        'template_rendered.receivers_for',
        'request_started.receivers_for',
        'request_finished.receivers_for',
        'request_tearing_down.receivers_for',
        'got_request_exception.receivers_for',
        'appcontext_tearing_down.receivers_for',
        'appcontext_pushed.receivers_for',
        'appcontext_popped.receivers_for',
        'message_flashed.receivers_for',

        # Top level props
        'after_this_request',
        'send_file',
        'jsonify',
        'render_template',
        'render_template_string',
        'templating._render',

        # DEV: Skipping this because it basically does `return send_file(join(directory, filename))`
        # 'send_from_directory',
    ]

    if flask_version >= (0, 11, 0):
        props.append('before_render_template.receivers_for')

    for prop in props:
        # Handle 'flask.request_started.receivers_for'
        obj = flask
        if '.' in prop:
            attr, _, prop = prop.partition('.')
            obj = getattr(obj, attr, object())
        _u(obj, prop)


@with_instance_pin
def traced_wsgi_app(pin, wrapped, instance, args, kwargs):
    """
    Wrapper for flask.app.Flask.wsgi_app

    This wrapper is the starting point for all requests.
    """
    # DEV: This is safe before this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    # Create a werkzeug request from the `environ` to make interacting with it easier
    # DEV: This executes before a request context is created
    request = werkzeug.Request(environ)

    # Configure distributed tracing
    if config.flask['distributed_tracing_enabled']:
        propagator = HTTPPropagator()
        context = propagator.extract(request.headers)
        # Only need to active the new context if something was propagated
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    # Default resource is method and path:
    #   GET /
    #   POST /save
    # We will override this below in `traced_dispatch_request` when we have a `RequestContext` and possibly a url rule
    resource = '{} {}'.format(request.method, request.path)
    with pin.tracer.trace('flask.request', service=pin.service, resource=resource, span_type=http.TYPE) as s:
        s.set_tag('flask.version', flask_version_str)

        # Flask version < 0.12.0 does not have `finalize_request`,
        # so we need to patch `start_response` instead
        if flask_version < (0, 12, 0):
            def _wrap_start_response(func):
                def traced_start_response(status_code, headers):
                    code, _, _ = status_code.partition(' ')
                    try:
                        code = int(code)
                    except ValueError:
                        pass

                    s.set_tag(http.STATUS_CODE, code)
                    if code in config.flask.get('error_codes', set()):
                        s.error = 1
                    return func(status_code, headers)
                return traced_start_response
            start_response = _wrap_start_response(start_response)

        # DEV: We set response status code in `traced_finalize_request`
        s.set_tag(http.URL, request.url)
        s.set_tag(http.METHOD, request.method)

        # TODO: Add request header tracing
        # for k, v in request.headers:
        #     s.set_tag('http.request.headers.{}'.format(k), v)
        return wrapped(environ, start_response)


def traced_blueprint_register(wrapped, instance, args, kwargs):
    """
    Wrapper for flask.blueprints.Blueprint.register

    This wrapper just ensures the blueprint has a pin, either set manually on
    itself from the user or inherited from the application
    """
    def _wrap(app, *args, **kwargs):
        pin = Pin.get_from(instance)
        if not pin:
            pin = Pin.get_from(app)
            if not pin:
                return wrapped(app, *args, **kwargs)
            pin.clone().onto(instance)
        return wrapped(app, *args, **kwargs)
    return _wrap(*args, **kwargs)


def traced_blueprint_add_url_rule(wrapped, instance, args, kwargs):
    pin = get_inherited_pin(wrapped, instance)
    if not pin:
        return wrapped(*args, **kwargs)

    def _wrap(rule, endpoint=None, view_func=None, **kwargs):
        if view_func:
            pin.clone().onto(view_func)
        return wrapped(rule, endpoint=endpoint, view_func=view_func, **kwargs)

    return _wrap(*args, **kwargs)


def traced_add_url_rule(wrapped, instance, args, kwargs):
    """Wrapper for flask.app.Flask.add_url_rule to wrap all views attached to this app"""
    def _wrap(rule, endpoint=None, view_func=None, **kwargs):
        if view_func:
            # TODO: `if hasattr(view_func, 'view_class')` then this was generated from a `flask.views.View`
            #   should we do something special with these views? Change the name/resource? Add tags?
            view_func = wrap_function(instance, view_func, name=endpoint, resource=rule)

        return wrapped(rule, endpoint=endpoint, view_func=view_func, **kwargs)

    return _wrap(*args, **kwargs)


def traced_endpoint(wrapped, instance, args, kwargs):
    """Wrapper for flask.app.Flask.endpoint to ensure all endpoints are wrapped"""
    def _wrap(endpoint):
        def _wrapper(func):
            name = func_name(func)
            return wrapped(endpoint)(wrap_function(instance, func, name=name, resource=endpoint))
        return _wrapper

    return _wrap(*args, **kwargs)


def traced_flask_hook(wrapped, instance, args, kwargs):
    """Wrapper for hook functions (before_request, after_request, etc) are properly traced"""
    def _wrap(func):
        return wrapped(wrap_function(instance, func))

    return _wrap(*args, **kwargs)


def traced_render_template(wrapped, instance, args, kwargs):
    """Wrapper for flask.templating.render_template"""
    pin = get_inherited_pin(wrapped, instance, get_current_app())
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.render_template', span_type=http.TEMPLATE):
        return wrapped(*args, **kwargs)


def traced_render_template_string(wrapped, instance, args, kwargs):
    """Wrapper for flask.templating.render_template_string"""
    pin = get_inherited_pin(wrapped, instance, get_current_app())
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.render_template_string', span_type=http.TEMPLATE):
        return wrapped(*args, **kwargs)


def traced_render(wrapped, instance, args, kwargs):
    """
    Wrapper for flask.templating._render


    This wrapper is used for setting template tags on the span.

    This method is called for render_template or render_template_string
    """
    pin = get_inherited_pin(wrapped, instance, get_current_app())
    # DEV: `get_current_span` will verify `pin` is valid and enabled first
    span = get_current_span(pin)
    if not span:
        return wrapped(*args, **kwargs)

    def _wrap(template, context, app):
        name = getattr(template, 'name', None) or config.flask.get('template_default_name')
        span.set_tag('template.name', name)
        # TODO: Anything else? Should we add tags for the context?
        return wrapped(*args, **kwargs)
    return _wrap(*args, **kwargs)


def traced_register_error_handler(wrapped, instance, args, kwargs):
    """Wrapper to trace all functions registered with flask.app.register_error_handler"""
    def _wrap(key, code_or_exception, f):
        return wrapped(key, code_or_exception, wrap_function(instance, f))
    return _wrap(*args, **kwargs)


@with_instance_pin
def traced_dispatch_request(pin, wrapped, instance, args, kwargs):
    """
    Wrapper to trace flask.app.Flask.dispatch_request


    This wrapper will add identifier tags to the current span from `flask.app.Flask.wsgi_app`.
    """
    span = get_current_span(pin)
    if not span:
        return wrapped(*args, **kwargs)

    try:
        request = flask._request_ctx_stack.top.request

        # DEV: This name will include the blueprint name as well (e.g. `bp.index`)
        if request.endpoint:
            span.resource = request.endpoint
            span.set_tag('flask.endpoint', request.endpoint)

        if request.url_rule and request.url_rule.rule:
            span.resource = '{} {}'.format(request.method, request.url_rule.rule)
            span.set_tag('flask.url_rule', request.url_rule.rule)

        if request.view_args and config.flask.get('collect_view_args'):
            for k, v in request.view_args.items():
                span.set_tag('flask.view_args.{}'.format(k), v)
    except Exception:
        # TODO: Log this exception
        pass

    with pin.tracer.trace('flask.dispatch_request', service=pin.service):
        return wrapped(*args, **kwargs)


@with_instance_pin
def traced_finalize_request(pin, wrapped, instance, args, kwargs):
    """
    Wrapper for flask.app.Flask.finalize_request

    This wrapper is used to set response tags on the span from `flask.app.Flask.wsgi_app`
    """
    span = get_current_span(pin, root=True)
    if not span:
        return wrapped(*args, **kwargs)

    response = None
    try:
        response = wrapped(*args, **kwargs)
        return response
    finally:
        if response:
            # TODO: Add response header tracing
            # for k, v in response.headers:
            #     s.set_tag('http.response.headers.{}'.format(k), v)
            span.set_tag(http.STATUS_CODE, response.status_code)

            # Mark this span as an error if we have a 500 response
            # DEV: We don't have any error info to set here
            # DEV: They may have handled this 500 error in code via a custom error handler
            if response.status_code in config.flask.get('error_codes', set()):
                span.error = 1


def traced_signal_receivers_for(signal):
    """Wrapper for flask.signals.{signal}.receivers_for to ensure all signal receivers are traced"""
    def outer(wrapped, instance, args, kwargs):
        def _wrap(sender, *args, **kwargs):
            # See if they gave us the flask.app.Flask as the sender
            app = None
            if isinstance(sender, flask.Flask):
                app = sender
            for receiver in wrapped(sender, *args ,**kwargs):
                yield wrap_signal(app, signal, receiver)

        return _wrap(*args, **kwargs)
    return outer


def traced_jsonify(wrapped, instance, args, kwargs):
    pin = get_inherited_pin(wrapped, instance, get_current_app())
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.jsonify'):
        return wrapped(*args, **kwargs)
