from ddtrace import config
import ddtrace.appsec._asm_request_context as _asmrc
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import function_wrapper

from .. import trace_utils
from ...internal.logger import get_logger
from ...internal.utils.importlib import func_name
from ...pin import Pin
from .helpers import get_current_app


log = get_logger(__name__)


def wrap_view(instance, func, name=None, resource=None):
    """
    Helper function to wrap common flask.app.Flask methods.

    This helper will first ensure that a Pin is available and enabled before tracing
    """
    if not name:
        name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = Pin._find(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask), resource=resource) as span:
            span.set_tag_str(COMPONENT, config.flask.integration_name)

            # if Appsec is enabled, we can try to block as we have the path parameters at that point
            if config._appsec_enabled and _asmrc.in_context():
                log.debug("Flask WAF call for Suspicious Request Blocking on request")
                if kwargs:
                    _asmrc.set_waf_address(SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, kwargs)
                _asmrc.call_waf_callback()
                if _asmrc.is_blocked():
                    callback_block = _asmrc.get_value(_asmrc._CALLBACKS, "flask_block")
                    if callback_block:
                        return callback_block()

            # If IAST is enabled, taint the Flask function kwargs (path parameters)
            if _is_iast_enabled() and kwargs:
                from ddtrace.appsec.iast._input_info import Input_info
                from ddtrace.appsec.iast._taint_tracking import taint_pyobject

                for k, v in kwargs.items():
                    kwargs[k] = taint_pyobject(v, Input_info(k, v, "http.request.path.parameter"))

            return wrapped(*args, **kwargs)

    return trace_func(func)


def wrap_function(instance, func, name=None, resource=None):
    """
    Helper function to wrap common flask.app.Flask methods.

    This helper will first ensure that a Pin is available and enabled before tracing
    """
    if not name:
        name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = Pin._find(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask), resource=resource) as span:
            span.set_tag_str(COMPONENT, config.flask.integration_name)
            return wrapped(*args, **kwargs)

    return trace_func(func)


def wrap_signal(app, signal, func):
    """
    Helper used to wrap signal handlers

    We will attempt to find the pin attached to the flask.app.Flask app
    """
    name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, instance, args, kwargs):
        pin = Pin._find(wrapped, instance, app, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask)) as span:
            span.set_tag_str(COMPONENT, config.flask.integration_name)

            span.set_tag_str("flask.signal", signal)
            return wrapped(*args, **kwargs)

    return trace_func(func)
