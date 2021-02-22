import os

from ddtrace import compat

from .uds import UDSHTTPConnection


def get_hostname():
    return os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", "localhost"))


def get_port():
    return int(os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT", 8126)))


def get_url():
    return os.environ.get("DD_TRACE_AGENT_URL", "http://%s:%d" % (get_hostname(), get_port()))


def get_connection(url, timeout):
    parsed = compat.parse.urlparse(url)
    port = parsed.port or 8126

    if parsed.scheme == "https":
        conn = compat.httplib.HTTPSConnection(parsed.hostname, port, timeout=timeout)
    elif parsed.scheme == "http":
        conn = compat.httplib.HTTPConnection(parsed.hostname, port, timeout=timeout)
    elif parsed.scheme == "unix":
        conn = UDSHTTPConnection(parsed.path, parsed.scheme == "https", parsed.hostname or "", port, timeout=timeout)
    else:
        raise ValueError("Unknown agent protocol %s" % parsed.scheme)

    return conn
