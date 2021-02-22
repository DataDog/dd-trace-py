import os

from ddtrace import compat
from ddtrace.utils.formats import get_env

from .uds import UDSHTTPConnection


def get_hostname():
    # type: () -> str
    return os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", "localhost"))


def get_trace_port():
    # type: () -> int
    return int(os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT", 8126)))


def get_stats_port():
    # type: () -> int
    return int(get_env("dogstatsd", "port", default=8125))


def get_trace_url():
    # type: () -> str
    return os.environ.get("DD_TRACE_AGENT_URL", "http://%s:%d" % (get_hostname(), get_trace_port()))


def get_stats_url():
    # type: () -> str
    return get_env("dogstatsd", "url", default="udp://{}:{}".format(get_hostname(), get_stats_port()))


def get_connection(url, timeout):
    """Return an HTTP connection to the given URL."""
    parsed = compat.parse.urlparse(url)
    hostname = parsed.hostname or ""

    if parsed.scheme == "https":
        conn = compat.httplib.HTTPSConnection(hostname, parsed.port, timeout=timeout)
    elif parsed.scheme == "http":
        conn = compat.httplib.HTTPConnection(hostname, parsed.port, timeout=timeout)
    elif parsed.scheme == "unix":
        conn = UDSHTTPConnection(parsed.path, parsed.scheme == "https", hostname, parsed.port, timeout=timeout)
    else:
        raise ValueError("Unknown agent protocol %s" % parsed.scheme)

    return conn
