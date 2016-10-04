
# stdib
import logging

# 3p
import requests

# project
import ddtrace
from ddtrace.compat import urlparse
from ddtrace.ext import http


log = logging.getLogger(__name__)


class TracedSession(requests.Session):

    def request(self, method, url, *args, **kwargs):
        tracer = self.get_datadog_tracer()

        # bail out if not enabled
        if not tracer.enabled:
            return super(TracedSession, self).request(method, url, *args, **kwargs)

        # otherwise trace the request
        with tracer.trace('requests.request') as span:
            # Do the response
            response = None

            try:
                response = super(TracedSession, self).request(method, url, *args, **kwargs)
                return response
            finally:

                # try to apply tags if we can
                try:
                    apply_tags(span, method, url, response)
                except Exception:
                    log.warn("error applying tags", exc_info=True)

    def get_datadog_tracer(self):
        return getattr(self, 'datadog_tracer', ddtrace.tracer)

    def set_datadog_tracer(self, tracer):
        setattr(self, 'datadog_tracer', tracer)

def apply_tags(span, method, url, response):
    try:
        parsed = urlparse.urlparse(url)
        span.service = parsed.netloc
        # FIXME[matt] how do we decide how do we normalize arbitrary urls???
    except Exception:
        pass

    span.set_tag(http.METHOD, method)
    span.set_tag(http.URL, url)
    if response is not None:
        span.set_tag(http.STATUS_CODE, response.status_code)
        span.error = 500 <= response.status_code
