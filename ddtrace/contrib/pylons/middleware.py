import logging

from ...ext import http
from ...ext import AppTypes

log = logging.getLogger(__name__)


class PylonsTraceMiddleware(object):

    def __init__(self, app, tracer, service="pylons"):
        self.app = app
        self._service = service
        self._tracer = tracer

        self._tracer.set_service_info(
            service=service,
            app="pylons",
            app_type=AppTypes.web,
        )

    def __call__(self, environ, start_response):
        with self._tracer.trace("pylons.request") as span:
            span.service = self._service
            span.span_type = http.TYPE

            if not span.sampled:
                return self.app(environ, start_response)

            # tentative on status code, otherwise will be caught by except below
            def _start_response(status, *args, **kwargs):
                """ a patched response callback which will pluck some metadata. """
                http_code = int(status.split()[0])
                span.set_tag(http.STATUS_CODE, http_code)
                if http_code >= 500:
                    span.error = 1
                return start_response(status, *args, **kwargs)

            try:
                return self.app(environ, _start_response)
            except Exception as e:
                # "unexpected errors"
                # exc_info set by __exit__ on current tracer
                span.set_tag(http.STATUS_CODE, getattr(e, 'code', 500))
                span.error = 1
                raise
            finally:
                controller = environ.get('pylons.routes_dict', {}).get('controller')
                action = environ.get('pylons.routes_dict', {}).get('action')

                # There are cases where users re-route requests and manually
                # set resources. If this is so, don't do anything, otherwise
                # set the resource to the controller / action that handled it.
                if span.resource == span.name:
                    span.resource = "%s.%s" % (controller, action)

                span.set_tags({
                    http.METHOD: environ.get('REQUEST_METHOD'),
                    http.URL: environ.get('PATH_INFO'),
                    "pylons.user": environ.get('REMOTE_USER', ''),
                    "pylons.route.controller": controller,
                    "pylons.route.action": action,
                })
