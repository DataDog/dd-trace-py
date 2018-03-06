import requests

from wrapt import wrap_function_wrapper as _w

from ...util import unwrap as _u
from .connection import _wrap_session_init, _wrap_request


def patch():
    """Activate http calls tracing"""
    if getattr(requests, '__datadog_patch', False):
        return
    setattr(requests, '__datadog_patch', True)

    _w('requests', 'Session.__init__', _wrap_session_init)
    _w('requests', 'Session.request', _wrap_request)


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, '__datadog_patch', False):
        return
    setattr(requests, '__datadog_patch', False)

    _u(requests.Session, '__init__')
    _u(requests.Session, 'request')
