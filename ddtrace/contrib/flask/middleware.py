"""
Datadog trace code for flask.

Requires a modern version of flask and the `blinker` library (which is a
dependency of flask signals).
"""

# stdlib
import time
import logging

# project
from ...ext import http, errors

# 3p
from flask import g, request, signals


class TraceMiddleware(object):

    def __init__(self, app, tracer, service="flask", use_signals=True):
        self.app = app
        self.app.logger.info("initializing trace middleware")

        # save our traces.
        self._tracer = tracer
        self._service = service

        self.use_signals = use_signals

        if self.use_signals and signals.signals_available:
            # if we're using signals, and things are correctly installed, use
            # signal hooks to track the responses.
            self.app.logger.info("connecting trace signals")
            signals.request_started.connect(self._request_started, sender=self.app)
            signals.request_finished.connect(self._request_finished, sender=self.app)
            signals.got_request_exception.connect(self._request_exception, sender=self.app)
            signals.before_render_template.connect(self._template_started, sender=self.app)
            signals.template_rendered.connect(self._template_done, sender=self.app)
        else:
            if self.use_signals: # warn the user that signals lib isn't installed
                self.app.logger.info(_blinker_not_installed_msg)

            # Fallback to using after request hook. Unfortunately, this won't
            # handle exceptions.
            self.app.before_request(self._before_request)
            self.app.after_request(self._after_request)

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
        """ Close and finsh the active span if it exists. """
        span = getattr(g, 'flask_datadog_span', None)
        if span:
            error = 0
            code = response.status_code if response else None

            # if we didn't get a response, but we did get an exception, set
            # codes accordingly.
            if not response and exception:
                error = 1
                code = 500
                span.set_tag(errors.ERROR_TYPE, type(exception))
                span.set_tag(errors.ERROR_MSG, exception)

            span.resource = str(request.endpoint or "").lower()
            span.set_tag(http.URL, str(request.base_url or ""))
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

_blinker_not_installed_msg = "please install blinker to use flask signals. http://flask.pocoo.org/docs/0.11/signals/"
