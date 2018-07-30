import logging
import sys

from webob import Request
from pylons import config

from .renderer import trace_rendering
from .constants import CONFIG_MIDDLEWARE

from ...ext import http, AppTypes
from ...propagation.http import HTTPPropagator

log = logging.getLogger(__name__)


class PylonsTraceMiddleware(object):

    def __init__(self, app, tracer, service='pylons', distributed_tracing=False):
        self.app = app
        self._service = service
        self._distributed_tracing = distributed_tracing
        self._tracer = tracer

        # register middleware reference
        config[CONFIG_MIDDLEWARE] = self

        # add template tracing
        trace_rendering()

        self._tracer.set_service_info(
            service=service,
            app="pylons",
            app_type=AppTypes.web,
        )

    def __call__(self, environ, start_response):
        if self._distributed_tracing:
            # retrieve distributed tracing headers
            request = Request(environ)
            propagator = HTTPPropagator()
            context = propagator.extract(request.headers)
            # only need to active the new context if something was propagated
            if context.trace_id:
                self._tracer.context_provider.activate(context)

        with self._tracer.trace("pylons.request", service=self._service) as span:
            # Set the service in tracer.trace() as priority sampling requires it to be
            # set as early as possible when different services share one single agent.
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
                # store current exceptions info so we can re-raise it later
                (typ, val, tb) = sys.exc_info()

                # e.code can either be a string or an int
                code = getattr(e, 'code', 500)
                try:
                    code = int(code)
                    if not 100 <= code < 600:
                        code = 500
                except:
                    code = 500
                span.set_tag(http.STATUS_CODE, code)
                span.error = 1

                # re-raise the original exception with its original traceback
                raise typ, val, tb
            except SystemExit:
                span.set_tag(http.STATUS_CODE, 500)
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
