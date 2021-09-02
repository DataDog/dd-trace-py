import os
from typing import Union

from ddtrace.internal.compat import parse
from ddtrace.utils.formats import get_env

from .http import HTTPConnection
from .http import HTTPSConnection
from .uds import UDSHTTPConnection


DEFAULT_HTTP_HOSTNAME = "localhost"
DEFAULT_HTTP_TRACE_PORT = 8126
DEFAULT_UNIX_TRACE_PATH = "/var/run/datadog/apm.socket"
DEFAULT_STATS_PORT = 8125
DEFAULT_TRACE_URL = "http://%s:%s" % (DEFAULT_HTTP_HOSTNAME, DEFAULT_HTTP_TRACE_PORT)
DEFAULT_TIMEOUT = 2.0

ConnectionType = Union[HTTPSConnection, HTTPConnection, UDSHTTPConnection]


def get_hostname():
    # type: () -> str
    return os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME", DEFAULT_HTTP_HOSTNAME))


def get_trace_port():
    # type: () -> int
    return int(os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT", DEFAULT_HTTP_TRACE_PORT)))


def get_stats_port():
    # type: () -> int
    return int(get_env("dogstatsd", "port", default=DEFAULT_STATS_PORT))  # type: ignore[arg-type]


def get_trace_agent_timeout():
    # type: () -> float
    return float(get_env("trace", "agent", "timeout", "seconds", default=DEFAULT_TIMEOUT))  # type: ignore[arg-type]


def get_trace_url():
    # type: () -> str
    """Return the Agent URL computed from the environment.

    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    # check these envars inside of method to make testing easier
    user_supplied_host = os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME")) is not None
    user_supplied_port = os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT")) is not None
    url = os.environ.get("DD_TRACE_AGENT_URL") 

    if not url: 
        if user_supplied_host or user_supplied_port:
            url = "http://%s:%s" % (get_hostname(), get_trace_port()) 
        elif os.path.exists("/var/run/datadog/apm.socket"):
                url = "unix://%s" % (DEFAULT_UNIX_TRACE_PATH) 
        else: url = DEFAULT_TRACE_URL 
                
    return url


def get_stats_url():
    # type: () -> str
    return get_env(
        "dogstatsd", "url", default="udp://{}:{}".format(get_hostname(), get_stats_port())
    )  # type: ignore[return-value]


def verify_url(url):
    # type: (str) -> parse.ParseResult
    """Verify that a URL can be used to communicate with the Datadog Agent.
    Returns a parse.ParseResult.
    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    parsed = parse.urlparse(url)
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
    # type: (str, float) -> ConnectionType
    """Return an HTTP connection to the given URL."""
    parsed = verify_url(url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"

    if parsed.scheme == "https":
        return HTTPSConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "http":
        return HTTPConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "unix":
        return UDSHTTPConnection(path, hostname, parsed.port, timeout=timeout)

    raise ValueError("Unsupported protocol '%s'" % parsed.scheme)
