"""
To trace the falcon web framework, install the trace middleware::

    import falcon
    from ddtrace import tracer
    from ddtrace.contrib.falcon import TraceMiddleware

    mw = TraceMiddleware(tracer, 'my-falcon-app')
    falcon.API(middleware=[mw])
"""
from ddtrace.ext import http as httpx, errors as errx


class TraceMiddleware(object):

    def __init__(self, tracer, service="falcon"):
        self.tracer = tracer
        self.service = service

    def process_request(self, req, resp):
        span = self.tracer.trace(
            "falcon.request",
            service=self.service,
            span_type=httpx.TYPE,
        )

        span.set_tag(httpx.METHOD, req.method)
        span.set_tag(httpx.URL, req.url)

    def process_resource(self, req, resp, resource, params):
        span = self.tracer.current_span()
        if not span:
            return  # unexpected
        span.resource = "%s %s" % (req.method, _name(resource))

    def process_response(self, req, resp, resource):
        span = self.tracer.current_span()
        if not span:
            return  # unexpected

        status = httpx.normalize_status_code(resp.status)

        # FIXME[matt] falcon does not map errors or unmatched routes
        # to proper status codes, so we we have to try to infer them
        # here. See https://github.com/falconry/falcon/issues/606
        if resource is None:
            span.resource = "%s 404" % req.method
            status = '404'

        # If we have an active unhandled error, treat it as a 500
        span.set_traceback()
        err_msg = span.get_tag(errx.ERROR_MSG)
        if err_msg and not _is_404(err_msg):
            status = '500'

        span.set_tag(httpx.STATUS_CODE, status)
        span.finish()

def _is_404(err_msg):
    return 'HTTPNotFound' in err_msg

def _name(r):
    return "%s.%s" % (r.__module__, r.__class__.__name__)
