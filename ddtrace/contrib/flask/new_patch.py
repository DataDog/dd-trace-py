"""
TODO:
  - What should the main resource name be?
    - METHOD + URL
    - METHOD + ENDPOINT
  - What should they be able to pin?
  - Test more exceptions
  - Think of other ways to register urls/endpoints
  - View classes (?)
  - Can we trace url rule lookup?
    - Worth it?
  - Reduce duplicate code (@with_pin ?)
  - What metadata can we grab from different places?
  - Can we access the app context in `wsgi_app`?
  - Do not double trace user code
    - e.g. if they add `@tracer.wrap()` to an endpoint, we should not wrap
    - Can we know this? What about decorator ordering?
      @app.route('/')
      @tracer.wrap()
      def endpoint(): pass

      vs

      # This likely traces the decorator created by `@app.route()`, but test to be sure
      @tracer.wrap()
      @app.route('/')
      def endpoint(): pass
  - Break up this file to better organize?
  - Can we get `def patch(flask)` to work?
  - distributed tracing
  - check existing patching to ensure we don't lose any functionality
"""
import flask
import werkzeug
from wrapt import function_wrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import Pin

from ...ext import AppTypes
from ...ext import http


def patch():
    """Patch the instrumented Flask object
    """
    # Check to see if we have patched Flask yet or not
    if getattr(flask, '_datadog_patch', False):
        return
    setattr(flask, '_datadog_patch', True)

    Pin(service='flask', app='flask', app_type=AppTypes.web).onto(flask.Flask)
    _w('flask', 'Flask.wsgi_app', traced_wsgi_app)
    _w('flask', 'Flask.dispatch_request', traced_dispatch_request)
    _w('flask', 'Flask.add_url_rule', traced_add_url_rule)
    _w('flask', 'Flask.endpoint', traced_endpoint)
    _w('flask', 'Flask._register_error_handler', traced_register_error_handler)
    _w('flask', 'Blueprint.register', traced_blueprint_register)

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
    ]
    for name in flask_app_traces:
        _w('flask', 'Flask.{}'.format(name), _simple_tracer('flask.{}'.format(name)))

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


def with_instance_pin(func):
    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        return func(pin, wrapped, instance, args, kwargs)
    return wrapper


def _simple_tracer(name, span_type=None):
    @with_instance_pin
    def wrapper(pin, wrapped, instance, args, kwargs):
        with pin.tracer.trace(name, service=pin.service, span_type=span_type):
            return wrapped(*args, **kwargs)
    return wrapper


@with_instance_pin
def traced_wsgi_app(pin, wrapped, instance, args, kwargs):
    # DEV: This is safe before this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    # DEV: You can't have Flask with Werkzeug
    request = werkzeug.Request(environ)

    # Default resource is method and path:
    #   GET /
    #   POST /save
    # We will override this below in `traced_dispatch_request` when we have a `RequestContext` and possibly a url rule
    resource = '{} {}'.format(request.method, request.path)
    with pin.tracer.trace('flask.request', service=pin.service, resource=resource, span_type=http.TYPE) as s:
        s.set_tag(http.URL, request.url)
        s.set_tag(http.METHOD, request.method)

        # TODO: Add request header tracing
        # for k, v in request.headers:
        #     s.set_tag('http.request.headers.{}'.format(k), v)

        def trace_response(status, headers):
            code, _, _ = status.partition(' ')
            s.set_tag(http.STATUS_CODE, code)
            # TODO: Add response header tracing
            # for k, v in headers:
            #     s.set_tag('http.response.headers.{}'.format(k), v)
            return start_response(status, headers)

        return wrapped(environ, trace_response)


def traced_blueprint_register(wrapped, instance, args, kwargs):
    def _wrap(app, *args, **kwargs):
        pin = Pin.get_from(instance)
        if not pin:
            pin = Pin.get_from(app)
            if not pin:
                return wrapped(app, *args, **kwargs)
            pin.clone().onto(instance)
        return wrapped(app, *args, **kwargs)
    return _wrap(*args, **kwargs)


def wrap_function(instance, func, name=None, resource=None):
    # TODO: Check to see if it is already wrapped
    #   Cannot do `if getattr(func, '__wrapped__', None)` because `functools.wraps` is used by third parties
    #   `isinstance(func, wrapt.ObjectProxy)` doesn't work because `tracer.wrap()` doesn't use `wrapt`
    if not name:
        name = '{}.{}'.format(func.__module__, func.__name__)

    @function_wrapper
    def trace_func(wrapped, _, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=pin.service, resource=resource):
            return wrapped(*args, **kwargs)

    return trace_func(func)


def traced_add_url_rule(wrapped, instance, args, kwargs):
    def _wrap(rule, endpoint=None, view_func=None, **kwargs):
        if view_func:
            view_func = wrap_function(instance, view_func, name=endpoint, resource=rule)

        return wrapped(rule, endpoint=endpoint, view_func=view_func, **kwargs)

    return _wrap(*args, **kwargs)


def traced_endpoint(wrapped, instance, args, kwargs):
    def _wrap(endpoint):
        def _wrapper(func):
            name = '{}.{}'.format(func.__module__, func.__name__)
            return wrapped(endpoint)(wrap_function(instance, func, name=name, resource=endpoint))
        return _wrapper

    return _wrap(*args, **kwargs)


def traced_flask_hook(wrapped, instance, args, kwargs):
    def _wrap(func):
        return wrapped(wrap_function(instance, func))

    return _wrap(*args, **kwargs)


def traced_render_template(wrapped, instance, args, kwargs):
    ctx = flask._app_ctx_stack.top
    if not ctx:
        return wrapped(*args, **kwargs)

    pin = Pin.get_from(ctx.app)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.templating.render_template', span_type=http.TEMPLATE):
        return wrapped(*args, **kwargs)


def traced_render_template_string(wrapped, instance, args, kwargs):
    ctx = flask._app_ctx_stack.top
    if not ctx:
        return wrapped(*args, **kwargs)

    pin = Pin.get_from(ctx.app)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    with pin.tracer.trace('flask.templating.render_template_string', span_type=http.TEMPLATE):
        return wrapped(*args, **kwargs)


def traced_render(wrapped, instance, args, kwargs):
    appctx = flask._app_ctx_stack.top
    if not appctx:
        return wrapped(*args, **kwargs)

    pin = Pin.get_from(appctx.app)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    ctx = pin.tracer.get_call_context()
    if not ctx:
        return wrapped(*args, **kwargs)

    span = ctx.get_current_span()
    if not span:
        return wrapped(*args, **kwargs)

    def _wrap(template, context, app):
        name = getattr(template, 'name', None) or '<memory>'
        span.set_tag('template.name', name)
        return wrapped(*args, **kwargs)
    return _wrap(*args, **kwargs)


def traced_register_error_handler(wrapped, instance, args, kwargs):
    def _wrap(key, code_or_exception, f):
        return wrapped(key, code_or_exception, wrap_function(instance, f))
    return _wrap(*args, **kwargs)


@with_instance_pin
def traced_dispatch_request(pin, wrapped, instance, args, kwargs):
    ctx = pin.tracer.get_call_context()
    if not ctx:
        return wrapped(*args, **kwargs)

    span = ctx.get_current_span()
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

        if request.view_args:
            for k, v in request.view_args.items():
                span.set_tag('flask.view_args.{}'.format(k), v)
    except Exception:
        pass

    with pin.tracer.trace('flask.dispatch_request', service=pin.service):
        return wrapped(*args, **kwargs)
