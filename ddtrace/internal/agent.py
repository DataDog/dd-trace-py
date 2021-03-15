import os

from ddtrace import compat
from ddtrace.utils.formats import get_env

from .uds import UDSHTTPConnection


DEFAULT_HOSTNAME = "localhost"
DEFAULT_TRACE_PORT = 8126
DEFAULT_STATS_PORT = 8125
DEFAULT_TRACE_URL = "http://%s:%s" % (DEFAULT_HOSTNAME, DEFAULT_TRACE_PORT)
DEFAULT_TIMEOUT = 2


def get_hostname():
    # type: () -> str
    return os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", DEFAULT_HOSTNAME))


def get_trace_port():
    # type: () -> int
    return int(os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT", DEFAULT_TRACE_PORT)))


def get_stats_port():
    # type: () -> int
    return int(get_env("dogstatsd", "port", default=DEFAULT_STATS_PORT))


def get_trace_url():
    # type: () -> str
    """Return the Agent URL computed from the environment.

    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    url = os.environ.get("DD_TRACE_AGENT_URL", "http://%s:%d" % (get_hostname(), get_trace_port()))
    return url


def get_stats_url():
    # type: () -> str
    return get_env("dogstatsd", "url", default="udp://{}:{}".format(get_hostname(), get_stats_port()))


def verify_url(url):
    # type: (str) -> compat.parse.ParseResult
    """Verify that a URL can be used to communicate with the Datadog Agent.

    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    parsed = compat.parse.urlparse(url)

    schemes = ("http", "https", "unix")
    if parsed.scheme not in schemes:
        raise ValueError(
            "Unsupported protocol '%s' in Agent URL '%s'. Must be one of: %s" % (parsed.scheme, url, ", ".join(schemes))
        )
    elif parsed.scheme in ["http", "https"] and not parsed.hostname:
        raise ValueError("Invalid hostname in Agent URL '%s'" % url)
    elif parsed.scheme == "unix" and not parsed.path:
        raise ValueError("Invalid file path in Agent URL '%s'" % url)

    return parsed


def get_connection(url, timeout=DEFAULT_TIMEOUT):
    # type: (str, float) -> compat.httplib.HTTPConnection
    """Return an HTTP connection to the given URL."""
    parsed = verify_url(url)
    hostname = parsed.hostname or ""

    if parsed.scheme == "https":
        conn = compat.httplib.HTTPSConnection(hostname, parsed.port, timeout=timeout)
    elif parsed.scheme == "http":
        conn = compat.httplib.HTTPConnection(hostname, parsed.port, timeout=timeout)
    elif parsed.scheme == "unix":
        conn = UDSHTTPConnection(parsed.path, parsed.scheme == "https", hostname, parsed.port, timeout=timeout)
    else:
        raise ValueError("Unsupported protocol '%s'" % parsed.scheme)

    return conn
