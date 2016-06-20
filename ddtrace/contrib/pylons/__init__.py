import logging

from tracer.ext import http

log = logging.getLogger(__name__)


class PylonsTraceMiddleware(object):

    def __init__(self, app, tracer, service="pylons"):
        self.app = app
        self._service = service
        self._tracer = tracer

    def __call__(self, environ, start_response):
        span = None
        try:
            span = self._tracer.trace("pylons.request", service=self._service, span_type=http.TYPE)
            log.debug("Initialize new trace %d", span.trace_id)

            def _start_response(status, *args, **kwargs):
                """ a patched response callback which will pluck some metadata. """
                span.span_type = http.TYPE
                http_code = int(status.split()[0])
                span.set_tag(http.STATUS_CODE, http_code)
                if http_code >= 500:
                    span.error = 1
                return start_response(status, *args, **kwargs)
        except Exception:
            log.exception("error starting span")

        try:
            return self.app(environ, _start_response)
        except Exception as e:
            if span:
                span.error = 1
            raise
        finally:
            if not span:
                return
            try:
                controller = environ.get('pylons.routes_dict', {}).get('controller')
                action = environ.get('pylons.routes_dict', {}).get('action')
                span.resource = "%s.%s" % (controller, action)

                span.set_tags({
                    http.METHOD: environ.get('REQUEST_METHOD'),
                    http.URL: environ.get('PATH_INFO'),
                    "pylons.user": environ.get('REMOTE_USER', ''),
                    "pylons.route.controller": controller,
                    "pylons.route.action": action,
                })
                span.finish()
            except Exception:
                log.exception("Error finishing trace")
