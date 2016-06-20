"""
Datadog trace code for flask.

Requires a modern version of flask and the `blinker` library (which is a
dependency of flask signals).
"""

# stdlib
import time
import logging

# 3p
from flask import g, request, signals


class TraceMiddleware(object):

    def __init__(self, app, tracer, service="flask", use_signals=True):
        self.app = app

        # save our traces.
        self._tracer = tracer
        self._service = service

        # Add our event handlers.
        self.app.before_request(self._before_request)

        if use_signals and signals.signals_available:
            # if we're using signals, and things are correctly installed, use
            # signal hooks to track the responses.
            signals.got_request_exception.connect(self._after_error, sender=self.app)
            signals.request_finished.connect(self._after_request, sender=self.app)
        else:
            if use_signals:
                # if we want to use signals, warn the user that blinker isn't
                # installed.
                self.app.logger.warn(_blinker_not_installed_msg)

            # Fallback to using after request hook. Unfortunately, this won't
            # handle errors.
            self.app.after_request(self._after_request)

    def _before_request(self):
        """ Starts tracing the current request and stores it in the global
            request object.
        """
        try:
            g.flask_datadog_span = self._tracer.trace(
                "flask.request",
                service=self._service)
        except Exception:
            self.app.logger.exception("error tracing request")

    def _after_error(self, *args, **kwargs):
        """ handles an error response. """
        exception = kwargs.pop("exception", None)
        try:
            self._finish_span(exception)
        except Exception:
            self.app.logger.exception("error tracing error")

    def _after_request(self, *args, **kwargs):
        """ handles a successful response. """
        try:
            self._finish_span()
        except Exception:
            self.app.logger.exception("error finishing trace")

    def _finish_span(self, exception=None):
        """ Close and finsh the active span if it exists. """
        span = getattr(g, 'flask_datadog_span', None)
        if span:
            span.resource = str(request.endpoint or "").lower()
            span.set_tag("http.url", str(request.base_url or ""))
            span.error = 1 if exception else 0
            span.finish()
            g.flask_datadog_span = None

_blinker_not_installed_msg = "please install blinker to use flask signals. http://flask.pocoo.org/docs/0.11/signals/"
