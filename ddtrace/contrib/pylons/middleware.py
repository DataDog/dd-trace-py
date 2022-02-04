import sys

from pylons import config
from webob import Request

from ddtrace import config as ddconfig

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import http
from ...internal.compat import reraise
from ...internal.logger import get_logger
from ...internal.utils.formats import asbool
from .constants import CONFIG_MIDDLEWARE
from .renderer import trace_rendering


log = get_logger(__name__)


class PylonsTraceMiddleware(object):
    def __init__(self, app, tracer, service="pylons", distributed_tracing=None):
        self.app = app
        self._service = service
        self._tracer = tracer

        if distributed_tracing is not None:
            self._distributed_tracing = distributed_tracing

        # register middleware reference
        config[CONFIG_MIDDLEWARE] = self

        # add template tracing
        trace_rendering()

    @property
    def _distributed_tracing(self):
        return ddconfig.pylons.distributed_tracing

    @_distributed_tracing.setter
    def _distributed_tracing(self, distributed_tracing):
        ddconfig.pylons["distributed_tracing"] = asbool(distributed_tracing)

    def __call__(self, environ, start_response):
        request = Request(environ)
        trace_utils.activate_distributed_headers(
            self._tracer, int_config=ddconfig.pylons, request_headers=request.headers
        )

        with self._tracer.trace("pylons.request", service=self._service, span_type=SpanTypes.WEB) as span:
            span.set_tag(SPAN_MEASURED_KEY)
            # Set the service in tracer.trace() as priority sampling requires it to be
            # set as early as possible when different services share one single agent.

            # set analytics sample rate with global config enabled
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, ddconfig.pylons.get_analytics_sample_rate(use_global_config=True))

            trace_utils.set_http_meta(span, ddconfig.pylons, request_headers=request.headers)

            if not span.sampled:
                return self.app(environ, start_response)

            # tentative on status code, otherwise will be caught by except below
            def _start_response(status, *args, **kwargs):
                """a patched response callback which will pluck some metadata."""
                if len(args):
                    response_headers = args[0]
                else:
                    response_headers = kwargs.get("response_headers", {})
                http_code = int(status.split()[0])
                trace_utils.set_http_meta(
                    span, ddconfig.pylons, status_code=http_code, response_headers=response_headers
                )
                return start_response(status, *args, **kwargs)

            try:
                return self.app(environ, _start_response)
            except Exception as e:
                # store current exceptions info so we can re-raise it later
                (typ, val, tb) = sys.exc_info()

                # e.code can either be a string or an int
                code = getattr(e, "code", 500)
                try:
                    int(code)
                except (TypeError, ValueError):
                    code = 500
                trace_utils.set_http_meta(span, ddconfig.pylons, status_code=code)

                # re-raise the original exception with its original traceback
                reraise(typ, val, tb=tb)
            except SystemExit:
                span.set_tag(http.STATUS_CODE, 500)
                span.error = 1
                raise
            finally:
                controller = environ.get("pylons.routes_dict", {}).get("controller")
                action = environ.get("pylons.routes_dict", {}).get("action")

                # There are cases where users re-route requests and manually
                # set resources. If this is so, don't do anything, otherwise
                # set the resource to the controller / action that handled it.
                if span.resource == span.name:
                    span.resource = "%s.%s" % (controller, action)

                url = "%s://%s:%s%s" % (
                    environ.get("wsgi.url_scheme"),
                    environ.get("SERVER_NAME"),
                    environ.get("SERVER_PORT"),
                    environ.get("PATH_INFO"),
                )
                trace_utils.set_http_meta(
                    span,
                    ddconfig.pylons,
                    method=environ.get("REQUEST_METHOD"),
                    url=url,
                    query=environ.get("QUERY_STRING"),
                )
                if controller:
                    span._set_str_tag("pylons.route.controller", controller)
                if action:
                    span._set_str_tag("pylons.route.action", action)
