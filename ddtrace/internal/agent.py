import json
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.settings._agent import config

from .utils.http import get_connection


log = get_logger(__name__)

_INFO: t.Optional[t.Dict[str, t.Any]] = None


def info(url=None) -> t.Optional[t.Dict[str, t.Any]]:
    global _INFO

    if _INFO is not None:
        return _INFO

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

    return (_INFO := json.loads(data))
