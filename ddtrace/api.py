# stdlib
import logging
import time

# project
from .encoding import get_encoder
from .compat import httplib


log = logging.getLogger(__name__)


class API(object):
    """
    Send data to the trace agent using the HTTP protocol and JSON format
    """
    def __init__(self, hostname, port, wait_response=False, headers=None, encoder=None):
        self.hostname = hostname
        self.port = port
        self._encoder = encoder or get_encoder()
        self._wait_response = wait_response

        # overwrite the Content-type with the one chosen in the Encoder
        self._headers = headers or {}
        self._headers.update({'Content-Type': self._encoder.content_type})

    def send_traces(self, traces):
        if not traces:
            return
        start = time.time()
        data = self._encoder.encode_traces(traces)
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
        data = self._encoder.encode_services(s)
        return self._put("/services", data)

    def _send_span_data(self, data):
        return self._put("/spans", data)

    def _put(self, endpoint, data):
        conn = httplib.HTTPConnection(self.hostname, self.port)
        conn.request("PUT", endpoint, data, self._headers)

        # read the server response only if the
        # API object is configured to do so
        if self._wait_response:
            return conn.getresponse()
