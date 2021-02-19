import os

from ddtrace import compat

from .uds import UDSHTTPConnection


HOSTNAME = os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", "localhost"))
PORT = int(os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT", 8126)))
URL = os.environ.get("DD_TRACE_AGENT_URL", "http://%s:%d" % (HOSTNAME, PORT))


def get_connection(url, timeout):
    parsed = compat.parse.urlparse(url)
    port = parsed.port or 8126

    if parsed.scheme == "https":
        conn = compat.httplib.HTTPSConnection(parsed.hostname, port, timeout=timeout)
    elif parsed.scheme == "http":
        conn = compat.httplib.HTTPConnection(parsed.hostname, port, timeout=timeout)
    elif parsed.scheme == "unix":
        conn = UDSHTTPConnection(parsed.path, False, parsed.hostname, port, timeout=timeout)
    else:
        raise ValueError("Unknown agent protocol %s" % parsed.scheme)

    return conn
