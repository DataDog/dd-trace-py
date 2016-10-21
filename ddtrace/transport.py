import logging

from .compat import httplib


log = logging.getLogger(__name__)


class Transport(object):
    """
    Transport interface that must be implemented to send data to
    the trace agent. Extend this class whenever a new transport
    system should be implemented.
    """
    def __init__(self, hostname, port):
        self.hostname = hostname
        self.port = port

    def send(self, method, endpoint, data, headers):
        """
        Send data to the trace agent. This method must be implemented
        because it is called by the asynchronous transport worker.
        """
        raise NotImplementedError


class HTTPTransport(Transport):
    """
    Send data to the trace agent using the HTTP protocol
    """
    DEFAULT_TIMEOUT = 1

    def send(self, method, url, payload):
        headers = { 'Content-Type': 'application/json' }
        try:
            conn = httplib.HTTPConnection(self.hostname, self.port, timeout=self.DEFAULT_TIMEOUT)
            conn.request(method, url, body=payload, headers=headers)
        except Exception:
            log.exception('Unable to flush data to the local agent')
