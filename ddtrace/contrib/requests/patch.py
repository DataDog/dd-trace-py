import os
import logging

import wrapt
import requests

import ddtrace

from ...ext import http
from ...propagation.http import HTTPPropagator
from ...util import asbool, unwrap as _u


log = logging.getLogger(__name__)


def patch():
    """Activate http calls tracing"""
    if getattr(requests, '__datadog_patch', False):
        return
    setattr(requests, '__datadog_patch', True)

    wrapt.wrap_function_wrapper('requests', 'Session.__init__', _session_initializer)
    wrapt.wrap_function_wrapper('requests', 'Session.request', _traced_request_func)


def unpatch():
    """Disable traced sessions"""
    if not getattr(requests, '__datadog_patch', False):
        return
    setattr(requests, '__datadog_patch', False)

    _u(requests.Session, '__init__')
    _u(requests.Session, 'request')


def _session_initializer(func, instance, args, kwargs):
    """Define settings when requests client is initialized"""
    func(*args, **kwargs)

    # set tracer settings
    distributed_tracing = asbool(os.environ.get('DATADOG_REQUESTS_DISTRIBUTED_TRACING')) or False
    setattr(instance, 'distributed_tracing', distributed_tracing)


def _traced_request_func(func, instance, args, kwargs):
    """ traced_request is a tracing wrapper for requests' Session.request
        instance method.
    """

    # perhaps a global tracer isn't what we want, so permit individual requests
    # sessions to have their own (with the standard global fallback)
    tracer = getattr(instance, 'datadog_tracer', ddtrace.tracer)

    # [TODO:christian] replace this with a unified way of handling options (eg, Pin)
    distributed_tracing = getattr(instance, 'distributed_tracing', None)

    # bail on the tracing if not enabled.
    if not tracer.enabled:
        return func(*args, **kwargs)

    method = kwargs.get('method') or args[0]
    url = kwargs.get('url') or args[1]
    headers = kwargs.get('headers', {})

    with tracer.trace("requests.request", span_type=http.TYPE) as span:
        if distributed_tracing:
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)
            kwargs['headers'] = headers

        resp = None
        try:
            resp = func(*args, **kwargs)
            return resp
        finally:
            try:
                _apply_tags(span, method, url, resp)
            except Exception:
                log.debug("error patching tags", exc_info=True)


def _apply_tags(span, method, url, response):
    """ apply_tags will patch the given span with tags about the given request. """
    span.set_tag(http.METHOD, method)
    span.set_tag(http.URL, url)
    if response is not None:
        span.set_tag(http.STATUS_CODE, response.status_code)
        # `span.error` must be an integer
        span.error = int(500 <= response.status_code)


class TracedSession(requests.Session):
    """ TracedSession is a requests' Session that is already patched.
    """
    pass


# Always patch our traced session with the traced method (cheesy way of sharing
# code)
wrapt.wrap_function_wrapper(TracedSession, 'request', _traced_request_func)
