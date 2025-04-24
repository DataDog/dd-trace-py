import json
import os
import socket
from typing import TYPE_CHECKING
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.settings._agent import config

from .utils.http import get_connection


DEFAULT_HOSTNAME = "localhost"
DEFAULT_TRACE_PORT = 8126
DEFAULT_UNIX_TRACE_PATH = "/var/run/datadog/apm.socket"
DEFAULT_UNIX_DSD_PATH = "/var/run/datadog/dsd.socket"
DEFAULT_STATS_PORT = 8125

if TYPE_CHECKING:  # pragma: no cover
    from .http import HTTPConnection
    from .http import HTTPSConnection
    from .uds import UDSHTTPConnection

    ConnectionType = Union[HTTPSConnection, HTTPConnection, UDSHTTPConnection]


log = get_logger(__name__)


def info(url=None):
    agent_url = config.trace_agent_url if url is None else url
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
