import flask
import werkzeug
from wrapt import function_wrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import Pin


def patch():
    """Patch the instrumented Flask object
    """
    if getattr(flask, '_datadog_patch', False):
        return

    setattr(flask, '_datadog_patch', True)

    Pin(service='flask', app='flask', app_type='web').onto(flask.Flask)
    _w('flask', 'Flask.wsgi_app', traced_wsgi_app)
    _w('flask', 'Flask.add_url_rule', traced_add_url_rule)
    _w('flask', 'Flask.before_request', traced_before_request)
    _w('flask', 'Flask.after_request', traced_after_request)

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
        _w('flask', 'Flask.{}'.format(name), _simple_tracer('flask.app.Flask.{}'.format(name)))


def _simple_tracer(name):
    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=pin.service):
            return wrapped(*args, **kwargs)
    return wrapper


def traced_wsgi_app(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # DEV: This is safe before this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    # DEV: You can't have Flask with Werkzeug
    request = werkzeug.Request(environ)

    # Examples:
    #   GET /
    #   POST /save
    resource = '{} {}'.format(request.method, request.path)
    with pin.tracer.trace('flask.app.Flask.wsgi_app', service=pin.service, resource=resource) as span:
        span.set_tag('http.url', request.url)
        span.set_tag('wsgi.request_method', request.method)
        span.set_tag('wsgi.path_info', request.path)
        span.set_tag('wsgi.remote_addr', request.remote_addr)

        if request.query_string:
            span.set_tag('wsgi.query_string', request.query_string)

        for k, v in request.headers:
            span.set_tag('wsgi.http.{}'.format(k), v)

        return wrapped(*args, **kwargs)

def wrap_function(pin, func, name=None):
    if not name:
        name = '{}.{}'.format(func.__module__, func.__name__)

    @function_wrapper
    def trace_func(wrapped, instance, args, kwargs):
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=pin.service):
            return wrapped(*args, **kwargs)

    return trace_func(func)


def traced_add_url_rule(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)

    def _wrap(rule, endpoint=None, view_func=None, **kwargs):
        if view_func:
            view_func = wrap_function(pin, view_func, name=endpoint)

        return wrapped(rule, endpoint=endpoint, view_func=view_func, **kwargs)

    return _wrap(*args, **kwargs)


def traced_before_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)

    def _wrap(func):
        return wrapped(wrap_function(pin, func))

    return _wrap(*args, **kwargs)


def traced_after_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)

    def _wrap(func):
        return wrapped(wrap_function(pin, func))

    return _wrap(*args, **kwargs)
