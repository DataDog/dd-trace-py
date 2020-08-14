import sys

import chardet  # TODO: vendor or make optional

import ddtrace
from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.vendor import six


log = get_logger(__name__)

config._add("wsgi", dict(service=config._get_service(default="wsgi")))


class DDTraceWrite(object):
    def __init__(self, write, tracer):
        self._write = write
        self._tracer = tracer
        self.injected_rum_header = False
        self._ncalls = 0

    def __call__(self, data):
        if not self.injected_rum_header and ddtrace.config.rum_header_injection:
            data = six.b("") + data
            self.injected_rum_header = True

        self._write(data)
        self._ncalls += 1


def parse_content_type(content_type):
    """Parse the value of the Content-Type header.

    Reference: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Type
    """
    media_type = None
    charset = None
    boundary = None
    if ";" not in content_type:
        media_type = content_type
    else:
        raw_vals = content_type.split(";")
        for i, val in enumerate(raw_vals):
            val = val.lower().strip()
            if "=" in val:
                k, v = val.split("=")
                if k == "media-type":
                    media_type = v
                elif k == "charset":
                    charset = v
                elif k == "boundary":
                    boundary = v
            elif i == 0:
                media_type = val
            elif i == 1:
                charset = val
            elif i == 2:
                boundary = val
    return media_type, charset, boundary


class DDWSGIMiddleware(object):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param service: Service name to use for the WSGI application.
    :param tracer: Tracer instance to use the middleware with. Defaults to the global tracer.
    """

    def __init__(self, application, service="wsgi", tracer=None):
        self.app = application
        self.tracer = tracer or ddtrace.tracer
        self.service = service
        self.inner_response_headers
        self.content_encoding = None
        self.content_length = None
        self.content_type = None
        self.rum_header_injected = False
        self.charset = None

    def do_rum_inject(self):
        if self.rum_header_injected:
            return False

        if not ddtrace.config.rum_header_injection:
            return False

        if not self.content_type:
            return False

        media_type, charset, boundary = self.content_type

        if media_type not in ["text/html"]:
            return False

        return True

    def __call__(self, environ, start_response):
        def intercept_start_response(status, response_headers, exc_info=None):
            self.inner_response_headers = response_headers

            for name, val in response_headers:
                name = name.lower()
                # tweak content-length
                if name == "content-type":
                    self.content_type = parse_content_type(val)
                elif name == "content-encoding":
                    self.content_encoding = val

            with self.tracer.trace("wsgi.start_response", service=self.service):
                write = start_response(status, response_headers, exc_info)
            return DDTraceWrite(write, self.tracer)

        with self.tracer.trace("wsgi.application", service=self.service) as span:
            try:
                result = self.app(environ, intercept_start_response)

                for chunk in result:
                    if self.do_rum_inject():
                        media_type, charset, _ = self.content_type
                        if not charset:
                            charset = chardet.detect(chunk)["encoding"]

                        try:
                            header = self.tracer.rum_header().encode(charset)
                        except LookupError:
                            log.error("Failed to encode RUM header.", exc_info=True)
                        else:
                            chunk = header + chunk
                            self.rum_header_injected = True

                        # TODO: update Content-Length and start_response()
                    yield chunk

                if hasattr(result, "close"):
                    try:
                        result.close()
                    except Exception:
                        typ, val, tb = sys.exc_info()
                        span.set_exc_info(typ, val, tb)
                        six.reraise(typ, val, tb=tb)
            except Exception:
                typ, val, tb = sys.exc_info()
                span.set_exc_info(typ, val, tb)
                six.reraise(typ, val, tb=tb)
