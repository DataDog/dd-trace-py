# stdlib
import logging
import time

# project
from .encoding import get_encoder, JSONEncoder
from .compat import httplib


log = logging.getLogger(__name__)


class API(object):
    """
    Send data to the trace agent using the HTTP protocol and JSON format
    """
    def __init__(self, hostname, port, headers=None, encoder=None):
        self.hostname = hostname
        self.port = port
        self._traces = '/v0.3/traces'
        self._services = '/v0.3/services'
        self._compatibility_mode = False
        self._encoder = encoder or get_encoder()

        # overwrite the Content-type with the one chosen in the Encoder
        self._headers = headers or {}
        self._headers.update({'Content-Type': self._encoder.content_type})

    def _downgrade(self):
        """
        Downgrades the used encoder and API level. This method must fallback to a safe
        encoder and API, so that it will success despite users' configurations. This action
        ensures that the compatibility mode is activated so that the downgrade will be
        executed only once.
        """
        self._compatibility_mode = True
        self._traces = '/v0.2/traces'
        self._services = '/v0.2/services'
        self._encoder = JSONEncoder()
        self._headers.update({'Content-Type': self._encoder.content_type})

    def send_traces(self, traces):
        if not traces:
            return
        start = time.time()
        data = self._encoder.encode_traces(traces)
        response = self._put(self._traces, data)

        # the API endpoint is not available so we should downgrade the connection and re-try the call
        if response.status in [404, 415] and self._compatibility_mode is False:
            log.debug('calling the endpoint "%s" but received %s; downgrading the API', self._traces, response.status)
            self._downgrade()
            return self.send_traces(traces)

        log.debug("reported %d spans in %.5fs", len(traces), time.time() - start)
        return response

    def send_services(self, services):
        if not services:
            return
        s = {}
        for service in services:
            s.update(service)
        data = self._encoder.encode_services(s)
        response = self._put(self._services, data)

        # the API endpoint is not available so we should downgrade the connection and re-try the call
        if response.status in [404, 415] and self._compatibility_mode is False:
            log.debug('calling the endpoint "%s" but received 404; downgrading the API', self._services)
            self._downgrade()
            return self.send_services(services)

        log.debug("reported %d services", len(services))
        return response

    def _put(self, endpoint, data):
        conn = httplib.HTTPConnection(self.hostname, self.port)
        conn.request("PUT", endpoint, data, self._headers)
        return conn.getresponse()
