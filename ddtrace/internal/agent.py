import json
import os
import socket
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace.internal.constants import DEFAULT_TIMEOUT
from ddtrace.internal.logger import get_logger
from ddtrace.settings._core import DDConfig

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


def _derive_trace_url(config: "AgentConfig") -> str:
    user_supplied_host = config._trace_agent_hostname or config._agent_host
    user_supplied_port = config._trace_agent_port or config._agent_port

    url = config._trace_agent_url
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


def _derive_stats_url(config: "AgentConfig") -> str:
    user_supplied_host = config._dogstatsd_host or config._agent_host
    user_supplied_port = config._dogstatsd_port or config._agent_port
    url = config._dogstatsd_url

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


class AgentConfig(DDConfig):
    __prefix__ = "dd"

    _trace_agent_hostname = DDConfig.v(
        Optional[str],
        "trace_agent_hostname",
        default=None,
        help_type="String",
        help="Legacy configuration, stores the hostname of the trace agent",
    )

    _trace_agent_port = DDConfig.v(
        Optional[int],
        "trace_agent_port",
        default=None,
        help_type="Int",
        help="Legacy configuration, stores the port of the trace agent",
    )

    _trace_agent_url = DDConfig.v(
        Optional[str],
        "trace_agent_url",
        default=None,
        help_type="String",
        help="Stores the URL of the trace agent",
    )

    trace_agent_timeout_seconds = DDConfig.v(
        float,
        "trace_agent_timeout_seconds",
        default=DEFAULT_TIMEOUT,
        help_type="Float",
        help="Stores the timeout in seconds for the trace agent",
    )

    _dogstatsd_host = DDConfig.v(
        Optional[str],
        "dogstatsd_host",
        default=None,
        help_type="String",
        help="Stores the hostname of the agent receiving DogStatsD metrics",
    )

    _dogstatsd_port = DDConfig.v(
        Optional[int],
        "dogstatsd_port",
        default=None,
        help_type="Int",
        help="Stores the port of the agent receiving DogStatsD metrics",
    )

    _dogstatsd_url = DDConfig.v(
        Optional[str],
        "dogstatsd_url",
        default=None,
        help_type="String",
        help="Stores the URL of the DogStatsD agent",
    )

    _agent_host = DDConfig.v(
        Optional[str],
        "agent_host",
        default=None,
        help_type="String",
        help="Stores the hostname of the agent",
    )

    _agent_port = DDConfig.v(
        Optional[int],
        "agent_port",
        default=None,
        help_type="Int",
        help="Stores the port of the agent",
    )

    trace_agent_url = DDConfig.d(str, _derive_trace_url)

    dogstatsd_url = DDConfig.d(str, _derive_stats_url)


config = AgentConfig()


def get_trace_url() -> str:
    """Return the Agent URL computed from the environment."""
    return config.trace_agent_url


def get_stats_url() -> str:
    """Return the DogStatsD URL computed from the environment."""
    return config.dogstatsd_url


def info(url=None):
    agent_url = get_trace_url() if url is None else url
    timeout = config.trace_agent_timeout_seconds
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
