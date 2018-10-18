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
import werkzeug
from wrapt import function_wrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import Pin

from ...ext import AppTypes
from ...ext import http
from ...propagation.http import HTTPPropagator
from .helpers import get_current_app, get_current_span, get_inherited_pin, func_name, simple_tracer, with_instance_pin


# TODO: This isn't the final form, waiting on Kyle for config api changes
config = {
    # Error codes that trigger the main span to be marked as an error
    'flask.response.error_codes': set([500, ]),

    # Flask service configuration
    'flask.service.name': 'flask',
    'flask.service.app': 'flask',
    'flask.service.app_type': AppTypes.web,

    # Default name for jinja2 templates
    # DEV: This is mostly used for in-memory/string templates (e.g. `render_template_string()`)
    'flask.template.default_name': '<memory>',

    # Whether we should collect `request.view_args` as tags
    'flask.collect_view_args': True,

    # Whether we should trace signal functions
    'flask.trace_signals': True,

    # Whether distributed tracing is enabled or not
    'flask.distributed_tracing.enabled': False,
}


def patch():
    """
    Patch `flask` module for tracing
    """
    # Check to see if we have patched Flask yet or not
    if getattr(flask, '_datadog_patch', False):
        return
    setattr(flask, '_datadog_patch', True)

    # TODO: Patch differently based on the version
    # TODO: Add a tag with the flask version?
    # version = tuple([int(i) for i in getattr(flask, '__version__', '0.0.0').split('.')])

    # Attach service pin to `flask.app.Flask`
    Pin(
        service=config.get('flask.service.name'),
        app=config.get('flask.service.app'),
        app_type=config.get('flask.service.app_type'),
    ).onto(flask.Flask)

    # flask.app.Flask methods that have custom tracing (add metadata, wrap functions, etc)
    _w('flask', 'Flask.wsgi_app', traced_wsgi_app)
    _w('flask', 'Flask.dispatch_request', traced_dispatch_request)
    _w('flask', 'Flask.finalize_request', traced_finalize_request)
    _w('flask', 'Flask.add_url_rule', traced_add_url_rule)
    _w('flask', 'Flask.endpoint', traced_endpoint)
    _w('flask', 'Flask._register_error_handler', traced_register_error_handler)

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
    if config.get('flask.trace_signals'):
        signals = [
            'template_rendered',
            'before_render_template',
            'request_started',
            'request_finished',
            'request_tearing_down',
            'got_request_exception',
            'appcontext_tearing_down',
            'appcontext_pushed',
            'appcontext_popped',
            'message_flashed',
        ]
        for signal in signals:
            _w('flask', '{}.connect'.format(signal), traced_signal_connect(signal))


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
    if config.get('flask.distributed_tracing.enabled'):
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
        if hasattr(flask, '__version__'):
            s.set_tag('flask.version', flask.__version__)
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


def wrap_function(instance, func, name=None, resource=None):
    """
    Helper function to wrap common flask.app.Flask methods.

    This helper will first ensure that a Pin is available and enabled before tracing
    """
    # TODO: Check to see if it is already wrapped
    #   Cannot do `if getattr(func, '__wrapped__', None)` because `functools.wraps` is used by third parties
    #   `isinstance(func, wrapt.ObjectProxy)` doesn't work because `tracer.wrap()` doesn't use `wrapt`
    if not name:
        name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = get_inherited_pin(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with pin.tracer.trace(name, service=pin.service, resource=resource):
            return wrapped(*args, **kwargs)

    return trace_func(func)


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
        name = getattr(template, 'name', None) or config.get('flask.template.default_name')
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

        if request.view_args and config.get('flask.collect_view_args'):
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
            if response.status_code in config.get('flask.response.error_codes', set()):
                span.error = 1


def wrap_signal(app, signal, func):
    """
    Helper used to wrap signal handlers

    We will attempt to find the pin attached to the flask.app.Flask app
    """
    name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, instance, args, kwargs):
        pin = get_inherited_pin(wrapped, instance, app, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=pin.service) as span:
            span.set_tag('flask.signal', signal)
            return wrapped(*args, **kwargs)

    return trace_func(func)


def traced_signal_connect(signal):
    """Wrapper for flask.signals.{signal}.connect to ensure all signal receivers are traced"""
    def outer(wrapped, instance, args, kwargs):
        def _wrap(receiver, *args, **kwargs):
            # See if they gave us the flask.app.Flask as the sender
            app = None
            if isinstance(kwargs.get('sender'), flask.Flask):
                app = kwargs['sender']
            elif len(args) > 0 and isinstance(args[0], flask.Flask):
                app = args[0]

            # We must mark as `weak=False` because the wrapt.FunctionWrapper we create is a weak reference
            if len(args) > 1:
                args = list(args)
                args[1] = False
                args = tuple(args)
            else:
                kwargs['weak'] = False
            return wrapped(wrap_signal(app, signal, receiver), *args, **kwargs)

        return _wrap(*args, **kwargs)
    return outer


def traced_jsonify(wrapped, instance, args, kwargs):
    pin = get_inherited_pin(wrapped, instance, get_current_app())
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.jsonify'):
        return wrapped(*args, **kwargs)
