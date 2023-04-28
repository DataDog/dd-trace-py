import json
import os
import socket
from typing import TypeVar
from typing import Union

from ddtrace.internal.compat import ensure_str
from ddtrace.internal.logger import get_logger

from .http import HTTPConnection
from .http import HTTPSConnection
from .uds import UDSHTTPConnection
from .utils.http import DEFAULT_TIMEOUT
from .utils.http import get_connection
from .utils.http import verify_url  # noqa


DEFAULT_HOSTNAME = "localhost"
DEFAULT_TRACE_PORT = 8126
DEFAULT_UNIX_TRACE_PATH = "/var/run/datadog/apm.socket"
DEFAULT_UNIX_DSD_PATH = "/var/run/datadog/dsd.socket"
DEFAULT_STATS_PORT = 8125
DEFAULT_TRACE_URL = "http://%s:%s" % (DEFAULT_HOSTNAME, DEFAULT_TRACE_PORT)

ConnectionType = Union[HTTPSConnection, HTTPConnection, UDSHTTPConnection]

T = TypeVar("T")

log = get_logger(__name__)


# This method returns if a hostname is an IPv6 address
def is_ipv6_hostname(hostname):
    # type: (Union[T, str]) -> bool
    if not isinstance(hostname, str):
        return False
    try:
        socket.inet_pton(socket.AF_INET6, hostname)
        return True
    except socket.error:  # not a valid address
        return False


def get_trace_hostname(default=DEFAULT_HOSTNAME):
    # type: (Union[T, str]) -> Union[T, str]
    hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_TRACE_AGENT_HOSTNAME", default))
    return "[{}]".format(hostname) if is_ipv6_hostname(hostname) else hostname


def get_stats_hostname(default=DEFAULT_HOSTNAME):
    # type: (Union[T, str]) -> Union[T, str]
    hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_DOGSTATSD_HOST", default))
    return "[{}]".format(hostname) if is_ipv6_hostname(hostname) else hostname


def get_trace_port(default=DEFAULT_TRACE_PORT):
    # type: (Union[T, int]) -> Union[T,int]
    v = os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT"))
    if v is not None:
        return int(v)
    return default


def get_stats_port(default=DEFAULT_STATS_PORT):
    # type: (Union[T, int]) -> Union[T,int]
    v = os.getenv("DD_DOGSTATSD_PORT", default=None)
    if v is not None:
        return int(v)
    return default


def get_trace_agent_timeout():
    # type: () -> float
    return float(os.getenv("DD_TRACE_AGENT_TIMEOUT_SECONDS", default=DEFAULT_TIMEOUT))


def get_trace_url():
    # type: () -> str
    """Return the Agent URL computed from the environment.

    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    user_supplied_host = get_trace_hostname(None) is not None
    user_supplied_port = get_trace_port(None) is not None

    url = os.environ.get("DD_TRACE_AGENT_URL")

    if not url:
        if user_supplied_host or user_supplied_port:
            url = "http://%s:%s" % (get_trace_hostname(), get_trace_port())
        elif os.path.exists("/var/run/datadog/apm.socket"):
            url = "unix://%s" % (DEFAULT_UNIX_TRACE_PATH)
        else:
            url = DEFAULT_TRACE_URL

    return url


def get_stats_url():
    # type: () -> str
    user_supplied_host = get_stats_hostname(None) is not None
    user_supplied_port = get_stats_port(None) is not None

    url = os.getenv("DD_DOGSTATSD_URL", default=None)

    if not url:
        if user_supplied_host or user_supplied_port:
            url = "udp://{}:{}".format(get_stats_hostname(), get_stats_port())
        elif os.path.exists("/var/run/datadog/dsd.socket"):
            url = "unix://%s" % (DEFAULT_UNIX_DSD_PATH)
        else:
            url = "udp://{}:{}".format(get_stats_hostname(), get_stats_port())
    return url


def info():
    agent_url = get_trace_url()
    _conn = get_connection(agent_url, timeout=get_trace_agent_timeout())
    try:
        _conn.request("GET", "info", headers={"content-type": "application/json"})
        resp = _conn.getresponse()
        data = resp.read()
    finally:
        _conn.close()

    if resp.status == 404:
        # Remote configuration is not enabled or unsupported by the agent
        return None

    if resp.status < 200 or resp.status >= 300:
        log.warning("Unexpected error: HTTP error status %s, reason %s", resp.status, resp.reason)
        return None

    return json.loads(ensure_str(data))
