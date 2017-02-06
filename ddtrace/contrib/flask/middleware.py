"""
Datadog trace code for flask.

Requires a modern version of flask and the `blinker` library (which is a
dependency of flask signals).
"""

# stdlib
import logging

# project
from ... import compat
from ...ext import http, errors, AppTypes

# 3p
import flask.templating
from flask import g, request, signals


log = logging.getLogger(__name__)


class TraceMiddleware(object):

    def __init__(self, app, tracer, service="flask", use_signals=True):
        self.app = app
        self.app.logger.info("initializing trace middleware")

        # save our traces.
        self._tracer = tracer
        self._service = service

        self._tracer.set_service_info(
            service=service,
            app="flask",
            app_type=AppTypes.web,
        )

        # warn the user if signals are unavailable (because blinker isn't
        # installed) if they are asking to use them.
        if use_signals and not signals.signals_available:
            self.app.logger.info(_blinker_not_installed_msg)
        self.use_signals = use_signals and signals.signals_available

        # instrument request timings
        timing_signals = {
            'request_started': self._request_started,
            'request_finished': self._request_finished,
            'got_request_exception': self._request_exception,
        }
        if self.use_signals and _signals_exist(timing_signals):
            self._connect(timing_signals)
        else:
            # Fallback to request hooks. Won't catch exceptions.
            # handle exceptions.
            self.app.before_request(self._before_request)
            self.app.after_request(self._after_request)

        # Instrument template rendering. If it's flask >= 0.11, we can use
        # signals, Otherwise we have to patch a global method.
        template_signals = {
            'before_render_template': self._template_started,  # added in 0.11
            'template_rendered': self._template_done
        }
        if self.use_signals and _signals_exist(template_signals):
            self._connect(template_signals)
        else:
            _patch_render(tracer)

    def _flask_signals_exist(self, names):
        """ Return true if the current version of flask has all of the given
            signals.
        """
        return all(getattr(signals, n, None) for n in names)

    def _connect(self, signal_to_handler):
        connected = True
        for name, handler in signal_to_handler.items():
            s = getattr(signals, name, None)
            if not s:
                connected = False
                log.warn("trying to instrument missing signal %s", name)
                continue
            s.connect(handler, sender=self.app)
        return connected

    # common methods

    def _start_span(self):
        try:
            g.flask_datadog_span = self._tracer.trace(
                "flask.request",
                service=self._service,
                span_type=http.TYPE,
            )
        except Exception:
            self.app.logger.exception("error tracing request")

    def _finish_span(self, response=None, exception=None):
        """ Close and finish the active span if it exists. """
        span = getattr(g, 'flask_datadog_span', None)
        if span:
            if span.sampled:
                error = 0
                code = response.status_code if response else None

                # if we didn't get a response, but we did get an exception, set
                # codes accordingly.
                if not response and exception:
                    error = 1
                    code = 500
                    span.set_tag(errors.ERROR_TYPE, type(exception))
                    span.set_tag(errors.ERROR_MSG, exception)

                # the endpoint that matched the request is None if an exception
                # happened so we fallback to a common resource
                resource = code if not request.endpoint else request.endpoint
                span.resource = compat.to_unicode(resource).lower()
                span.set_tag(http.URL, compat.to_unicode(request.base_url or ''))
                span.set_tag(http.STATUS_CODE, code)
                span.error = error
            span.finish()
            # Clear our span just in case.
            g.flask_datadog_span = None

    # Request hook methods

    def _before_request(self):
        """ Starts tracing the current request and stores it in the global
            request object.
        """
        self._start_span()

    def _after_request(self, response):
        """ handles a successful response. """
        try:
            self._finish_span(response=response)
        except Exception:
            self.app.logger.exception("error finishing trace")
        finally:
            return response

    # signal handling methods

    def _request_started(self, sender):
        self._start_span()

    def _request_finished(self, sender, response, **kwargs):
        try:
            self._finish_span(response=response)
        except Exception:
            self.app.logger.exception("error finishing trace")
        return response

    def _request_exception(self, *args, **kwargs):
        """ handles an error response. """
        exception = kwargs.pop("exception", None)
        try:
            self._finish_span(exception=exception)
        except Exception:
            self.app.logger.exception("error tracing error")

    def _template_started(self, sender, template, *args, **kwargs):
        span = self._tracer.trace('flask.template')
        try:
            span.span_type = http.TEMPLATE
            span.set_tag("flask.template", template.name or "string")
        finally:
            g.flask_datadog_tmpl_span = span

    def _template_done(self, *arg, **kwargs):
        span = getattr(g, 'flask_datadog_tmpl_span', None)
        if span:
            span.finish()


def _patch_render(tracer):
    """ patch flask's render template methods with the given tracer. """
    # fall back to patching  global method
    _render = flask.templating._render

    def _traced_render(template, context, app):
        with tracer.trace('flask.template') as span:
            span.span_type = http.TEMPLATE
            span.set_tag("flask.template", template.name or "string")
            return _render(template, context, app)

    flask.templating._render = _traced_render


def _signals_exist(names):
    """ Return true if all of the given signals exist in this version of flask.
    """
    return all(getattr(signals, n, False) for n in names)


_blinker_not_installed_msg = (
    "please install blinker to use flask signals. "
    "http://flask.pocoo.org/docs/0.11/signals/"
)
