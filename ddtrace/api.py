# stdlib
import logging
import time

# project
import ddtrace.encoding
from .compat import httplib


log = logging.getLogger(__name__)


class API(object):
    """
    Send data to the trace agent using the HTTP protocol and JSON format
    """
    def __init__(self, hostname, port, wait_response=False):
        self.hostname = hostname
        self.port = port
        self.headers = { 'Content-Type': 'application/json' }
        self._wait_response = wait_response

    def send_traces(self, traces):
        if not traces:
            return
        start = time.time()
        data = ddtrace.encoding.encode_traces(traces)
        response = self._send_span_data(data)
        log.debug("reported %d spans in %.5fs", len(traces), time.time() - start)
        return response

    def send_services(self, services):
        if not services:
            return
        log.debug("Reporting %d services", len(services))
        s = {}
        for service in services:
            s.update(service)
        data = ddtrace.encoding.encode_services(s)
        return self._put("/services", data, self.headers)

    def _send_span_data(self, data):
        return self._put("/spans", data, self.headers)

    def _put(self, endpoint, data, headers):
        conn = httplib.HTTPConnection(self.hostname, self.port)
        conn.request("PUT", endpoint, data, self.headers)

        # read the server response only if the
        # API object is configured to do so
        if self._wait_response:
            return conn.getresponse()
