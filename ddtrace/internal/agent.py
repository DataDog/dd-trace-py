import json
import os
import socket
from typing import TypeVar
from typing import Union

from ddtrace.internal.constants import DEFAULT_TIMEOUT
from ddtrace.internal.logger import get_logger

from .http import HTTPConnection
from .http import HTTPSConnection
from .uds import UDSHTTPConnection
from .utils.http import get_connection


DEFAULT_HOSTNAME = "localhost"
DEFAULT_TRACE_PORT = 8126
DEFAULT_UNIX_TRACE_PATH = "/var/run/datadog/apm.socket"
DEFAULT_UNIX_DSD_PATH = "/var/run/datadog/dsd.socket"
DEFAULT_STATS_PORT = 8125

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


def get_trace_url():
    # type: () -> str
    """Return the Agent URL computed from the environment.

    Raises a ``ValueError`` if the URL is not supported by the Agent.
    """
    user_supplied_host = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_TRACE_AGENT_HOSTNAME"))
    user_supplied_port = os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT"))

    url = os.environ.get("DD_TRACE_AGENT_URL")

    if not url:
        if user_supplied_host is not None or user_supplied_port is not None:
            host = user_supplied_host or DEFAULT_HOSTNAME
            port = user_supplied_port or DEFAULT_TRACE_PORT
            if is_ipv6_hostname(host):
                host = "[{}]".format(host)
            url = "http://%s:%s" % (host, port)
        elif os.path.exists("/var/run/datadog/apm.socket"):
            url = "unix://%s" % (DEFAULT_UNIX_TRACE_PATH)
        else:
            url = "http://{}:{}".format(DEFAULT_HOSTNAME, DEFAULT_TRACE_PORT)

    return url


def get_stats_url():
    # type: () -> str
    user_supplied_host = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_DOGSTATSD_HOST"))
    user_supplied_port = os.getenv("DD_DOGSTATSD_PORT")
    url = os.getenv("DD_DOGSTATSD_URL")

    if not url:
        if user_supplied_host is not None or user_supplied_port is not None:
            port = user_supplied_port or DEFAULT_STATS_PORT
            host = user_supplied_host or DEFAULT_HOSTNAME
            if is_ipv6_hostname(host):
                host = "[{}]".format(host)
            url = "udp://{}:{}".format(host, port)
        elif os.path.exists("/var/run/datadog/dsd.socket"):
            url = "unix://%s" % (DEFAULT_UNIX_DSD_PATH)
        else:
            url = "udp://{}:{}".format(DEFAULT_HOSTNAME, DEFAULT_STATS_PORT)
    return url


def info(url=None):
    agent_url = get_trace_url() if url is None else url
    timeout = float(os.getenv("DD_TRACE_AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    _conn = get_connection(agent_url, timeout=timeout)
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

    return json.loads(data)
