from flask import g
from flask import request
from flask import signals
import flask.templating

from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from .. import trace_utils
from ...ext import SpanTypes
from ...ext import http
from ...internal import compat
from ...internal.logger import get_logger
from ...internal.utils.cache import cached
from ...vendor.debtcollector.removals import remove


log = get_logger(__name__)


SPAN_NAME = "flask.request"


@cached()
def _normalize_resource(resource):
    return compat.to_unicode(resource).lower()


class TraceMiddleware(object):
    @remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
    def __init__(self, app, tracer, service="flask", use_signals=True, distributed_tracing=None):
        self.app = app
        log.debug("flask: initializing trace middleware")

        # Attach settings to the inner application middleware. This is required if double
        # instrumentation happens (i.e. `ddtrace-run` with `TraceMiddleware`). In that
        # case, `ddtrace-run` instruments the application, but then users code is unable
        # to update settings such as `distributed_tracing` flag. This step can be removed
        # when the `Config` object is used
        self.app._tracer = tracer
        self.app._service = service
        self.app._use_distributed_tracing = distributed_tracing
        self.use_signals = use_signals

        # safe-guard to avoid double instrumentation
        if getattr(app, "__dd_instrumentation", False):
            return
        setattr(app, "__dd_instrumentation", True)

        # Install hooks which time requests.
        self.app.before_request(self._before_request)
        self.app.after_request(self._after_request)
        self.app.teardown_request(self._teardown_request)

        # Add exception handling signals. This will annotate exceptions that
        # are caught and handled in custom user code.
        # See https://github.com/DataDog/dd-trace-py/issues/390
        if use_signals and not signals.signals_available:
            log.debug(_blinker_not_installed_msg)
        self.use_signals = use_signals and signals.signals_available
        timing_signals = {
            "got_request_exception": self._request_exception,
        }
        self._receivers = []
        if self.use_signals and _signals_exist(timing_signals):
            self._connect(timing_signals)

        _patch_render(tracer)

    def _connect(self, signal_to_handler):
        connected = True
        for name, handler in signal_to_handler.items():
            s = getattr(signals, name, None)
            if s is None:
                connected = False
                log.warning("trying to instrument missing signal %s", name)
                continue
            # we should connect to the signal without using weak references
            # otherwise they will be garbage collected and our handlers
            # will be disconnected after the first call; for more details check:
            # https://github.com/jek/blinker/blob/207446f2d97/blinker/base.py#L106-L108
            s.connect(handler, sender=self.app, weak=False)
            self._receivers.append(handler)
        return connected

    def _before_request(self):
        """Starts tracing the current request and stores it in the global
        request object.
        """
        self._start_span()

    def _after_request(self, response):
        """Runs after the server can process a response."""
        try:
            self._process_response(response)
        except Exception:
            log.debug("flask: error tracing response", exc_info=True)
        return response

    def _teardown_request(self, exception):
        """Runs at the end of a request. If there's an unhandled exception, it
        will be passed in.
        """
        # when we teardown the span, ensure we have a clean slate.
        span = g.__dict__.pop("flask_datadog_span", None)
        if span is None:
            return

        try:
            self._finish_span(span, exception=exception)
        except Exception:
            log.debug("flask: error finishing span", exc_info=True)

    def _start_span(self):
        trace_utils.activate_distributed_headers(
            self.app._tracer,
            int_config=config.flask,
            request_headers=request.headers,
            override=self.app._use_distributed_tracing,
        )

        try:
            g.flask_datadog_span = self.app._tracer.trace(
                SPAN_NAME,
                service=self.app._service,
                span_type=SpanTypes.WEB,
            )
        except Exception:
            log.debug("flask: error tracing request", exc_info=True)

    def _process_response(self, response):
        span = getattr(g, "flask_datadog_span", None)
        if not span:
            return

        code = response.status_code if response else ""
        trace_utils.set_http_meta(span, config.flask, status_code=code, response_headers=response.headers)

    def _request_exception(self, *args, **kwargs):
        exception = kwargs.get("exception", None)
        span = getattr(g, "flask_datadog_span", None)
        if span and exception:
            _set_error_on_span(span, exception)

    def _finish_span(self, span, exception=None):
        if not span:
            return

        code = span.get_tag(http.STATUS_CODE) or 0
        try:
            code = int(code)
        except Exception:
            code = 0

        if exception:
            # if the request has already had a code set, don't override it.
            code = code or 500
            _set_error_on_span(span, exception)

        # the endpoint that matched the request is None if an exception
        # happened so we fallback to a common resource
        span.error = 0 if code < 500 else 1

        # the request isn't guaranteed to exist here, so only use it carefully.
        method = ""
        endpoint = ""
        url = ""
        query = ""
        request_headers = ""

        if request:
            method = request.method
            endpoint = request.endpoint or code
            url = request.base_url or ""
            query = request.query_string.decode() if hasattr(request.query_string, "decode") else request.query_string
            request_headers = request.headers

        # Let users specify their own resource in middleware if they so desire.
        # See case https://github.com/DataDog/dd-trace-py/issues/353
        if span.resource == SPAN_NAME:
            resource = endpoint or code
            span.resource = _normalize_resource(resource)

        trace_utils.set_http_meta(
            span,
            config.flask,
            method=method,
            url=compat.to_unicode(url),
            status_code=code,
            query=query,
            request_headers=request_headers,
        )
        span.finish()


def _set_error_on_span(span, exception):
    # The 3 next lines might not be strictly required, since `set_traceback`
    # also get the exception from the sys.exc_info (and fill the error meta).
    # Since we aren't sure it always work/for insuring no BC break, keep
    # these lines which get overridden anyway.
    span.set_tag(ERROR_TYPE, type(exception))
    span.set_tag(ERROR_MSG, exception)
    # The provided `exception` object doesn't have a stack trace attached,
    # so attach the stack trace with `set_traceback`.
    span.set_traceback()


def _patch_render(tracer):
    """patch flask's render template methods with the given tracer."""
    # fall back to patching  global method
    _render = flask.templating._render

    # If the method has already been patched and we're patching again then
    # we have to patch again with the new tracer reference.
    if hasattr(_render, "__dd_orig"):
        _render = getattr(_render, "__dd_orig")

    def _traced_render(template, context, app):
        with tracer.trace("flask.template", span_type=SpanTypes.TEMPLATE) as span:
            span._set_str_tag("flask.template", compat.maybe_stringify(template.name) or "string")
            return _render(template, context, app)

    setattr(_traced_render, "__dd_orig", _render)
    flask.templating._render = _traced_render


def _signals_exist(names):
    """Return true if all of the given signals exist in this version of flask."""
    return all(getattr(signals, n, False) for n in names)


_blinker_not_installed_msg = "please install blinker to use flask signals. " "http://flask.pocoo.org/docs/0.11/signals/"
