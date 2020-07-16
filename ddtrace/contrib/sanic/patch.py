from sanic import Sanic

from ddtrace import config
from ddtrace import tracer as global_tracer
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...utils.formats import asbool, get_env
from ...internal.logger import get_logger


log = get_logger(__name__)

def patch():
    """Patch the instrumented methods.
    """
    if getattr(Sanic, '__datadog_patch', 'False'):
        return
    setattr(Sanic, '__datadog_patch', True)
    _w('sanic', 'Sanic.handle_request', patch_handle_request)


def patch_handle_request(wrapped, instance, args, kwargs):
    """Wrapper for Sanic.handle_request"""
    def _wrap(request, write_callback, stream_callback):
        # import pdb; pdb.set_trace()
        log.debug("I have intercepted!")
        return wrapped(request, write_callback, stream_callback, **kwargs)

    return _wrap(**args, **kwargs)