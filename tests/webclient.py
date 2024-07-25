import urllib.parse

import requests

from ddtrace._trace.context import Context
from ddtrace.filters import TraceFilter
from ddtrace.internal.utils.retry import retry
from ddtrace.propagation.http import HTTPPropagator


class Client(object):
    """HTTP Client for making requests to a local http server."""

    def __init__(self, base_url):
        # type: (str) -> None
        self._base_url = base_url
        self._session = requests.Session()
        # Propagate traces with trace_id = 1 for the ping trace so we can filter them out.
        c, d = Context(trace_id=1, span_id=1), {}
        HTTPPropagator.inject(c, d)
        self._ignore_headers = d

    def _url(self, path):
        # type: (str) -> str
        return urllib.parse.urljoin(self._base_url, path)

    def get(self, path, **kwargs):
        return self._session.get(self._url(path), **kwargs)

    def get_ignored(self, path, **kwargs):
        """Do a normal get request but signal that the trace should be filtered out.

        The signal is a distributed trace id header with the value 1.
        """
        headers = kwargs.get("headers", {}).copy()
        headers.update(self._ignore_headers)
        kwargs["headers"] = headers
        return self._session.get(self._url(path), **kwargs)

    def post(self, path, *args, **kwargs):
        return self._session.post(self._url(path), *args, **kwargs)

    def request(self, method, path, *args, **kwargs):
        return self._session.request(method, self._url(path), *args, **kwargs)

    def wait(self, path="/", max_tries=100, delay=0.1, initial_wait=0):
        # type: (str, int, float) -> None
        """Wait for the server to start by repeatedly http `get`ting `path` until a 200 is received."""

        @retry(after=[delay] * (max_tries - 1), initial_wait=initial_wait)
        def ping():
            r = self.get_ignored(path, timeout=1)
            assert r.status_code == 200

        ping()


class PingFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all traces with trace_id = 1
        # This is done to prevent certain traces from being included in snapshots and
        # accomplished by propagating an http trace id of 1 with the request to the webserver.
        return None if trace and trace[0].trace_id == 1 else trace
