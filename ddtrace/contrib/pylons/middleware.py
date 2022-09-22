import json
import sys
from typing import Any
from typing import Dict
from typing import Tuple

from pylons import config
import webob
from webob import Request
import xmltodict

from ddtrace import config as ddconfig
from ddtrace.internal.compat import iteritems

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


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

log = get_logger(__name__)
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


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

    def _unlist_multidict_params(self, multidict):  # type: (webob.multidict.MultiDict) -> Dict[str, Any]
        # Pylons multidict always return values in a list. This will convert lists with a
        # single value
        # into the un-listed value
        new_dict = {}  # type: Dict[str, Any]

        for k, v in iteritems(multidict):
            if isinstance(v, list) and len(v) == 1:
                new_dict[k] = v[0]
            else:
                new_dict[k] = v

        return new_dict

    def _parse_path_params(self, pylon_path_params):  # type: (Tuple[Any, Dict[str, Any]]) -> Dict[str, Any]
        path_params = {}
        if len(pylon_path_params) > 0:
            path_params = {k: v for k, v in iteritems(pylon_path_params[1].copy()) if k not in ["action", "controller"]}
        return path_params

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

            req_body = None

            if ddconfig._appsec_enabled and request.method in _BODY_METHODS:
                content_type = getattr(request, "content_type", request.headers.environ.get("CONTENT_TYPE"))

                try:
                    if content_type == "application/x-www-form-urlencoded":
                        req_body = self._unlist_multidict_params(request.POST.dict_of_lists())
                    elif content_type == "application/json":
                        # if pylons>=0.10,<0.11 request.json not exists
                        if hasattr(request, "json"):
                            req_body = request.json
                        else:
                            req_body = json.loads(request.body.decode("UTF-8"))
                    elif content_type in ("application/xml", "text/xml"):
                        req_body = xmltodict.parse(request.body.decode("UTF-8"))
                    else:  # text/plain, xml, others: take them as strings
                        req_body = request.body.decode("UTF-8")

                except (AttributeError, OSError, ValueError, JSONDecodeError):
                    log.warning("Failed to parse request body", exc_info=True)
                    # req_body is None

            trace_utils.set_http_meta(
                span,
                ddconfig.pylons,
                method=request.method,
                request_headers=dict(request.headers),
                request_cookies=dict(request.cookies),
                request_body=req_body,
                headers_are_case_sensitive=True,
            )

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
                    span,
                    ddconfig.pylons,
                    method=request.method,
                    status_code=http_code,
                    response_headers=response_headers,
                    headers_are_case_sensitive=True,
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
                path_params = environ.get("wsgiorg.routing_args", [])
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

                query_string = environ.get("QUERY_STRING")

                raw_uri = url
                if raw_uri and query_string and ddconfig.pylons.trace_query_string:
                    raw_uri += "?" + query_string

                trace_utils.set_http_meta(
                    span,
                    ddconfig.pylons,
                    method=environ.get("REQUEST_METHOD"),
                    url=url,
                    raw_uri=raw_uri,
                    query=query_string,
                    parsed_query=dict(request.GET),
                    request_path_params=self._parse_path_params(path_params),
                )
                if controller:
                    span.set_tag_str("pylons.route.controller", controller)
                if action:
                    span.set_tag_str("pylons.route.action", action)
