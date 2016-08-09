
from ddtrace.buffer import ThreadLocalSpanBuffer
from ddtrace.ext import http as httpx, errors as errx


class TraceMiddleware(object):

    def __init__(self, tracer, service="falcon"):
        self.tracer = tracer
        self.service = service
        self.buffer = ThreadLocalSpanBuffer()

    def process_request(self, req, resp):
        self.buffer.pop()  # we should never really have anything here.

        span = self.tracer.trace(
            "falcon.request",
            service=self.service,
            span_type=httpx.TYPE,
        )

        span.set_tag(httpx.METHOD, req.method)
        span.set_tag(httpx.URL, req.url)

        self.buffer.set(span)

    def process_resource(self, req, resp, resource, params):
        span = self.buffer.get()
        if not span:
            return  # unexpected
        span.resource = "%s %s" % (req.method, _name(resource))

    def process_response(self, req, resp, resource):
        span = self.buffer.pop()
        if not span:
            return  # unexpected

        status = httpx.normalize_status_code(resp.status)

        # if we never mapped to a resource, note this is a 400.
        if resource is None:
            span.resource = "%s 404" % req.method
            status = '404'

        # falcon does not map unhandled errors to status codes
        # before this runs, so we have to try to infer status codes
        # if we have an unhandled error.
        span.set_traceback()
        err_msg = span.get_tag(errx.ERROR_MSG)
        if err_msg:
            status = '404' if 'HTTPNotFound' in err_msg else '500'

        span.set_tag(httpx.STATUS_CODE, status)
        span.finish()


def _name(r):
    return "%s.%s" % (r.__module__, r.__class__.__name__)
