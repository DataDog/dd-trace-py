import abc
import json
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.settings._agent import config

from .utils.http import get_connection


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


class AgentCheckPeriodicService(ForksafeAwakeablePeriodicService, metaclass=abc.ABCMeta):
    def __init__(self, interval: float = 0.0):
        super().__init__(interval=interval)

        self._state = self._agent_check

    @abc.abstractmethod
    def info_check(self, agent_info: t.Optional[dict]) -> bool:
        ...

    def _agent_check(self) -> None:
        try:
            agent_info = info()
        except Exception:
            agent_info = None

        if self.info_check(agent_info):
            self._state = self._online
            self._online()

    def _online(self) -> None:
        try:
            self.online()
        except Exception:
            self._state = self._agent_check
            log.debug("Error during online operation, reverting to agent check", exc_info=True)

    @abc.abstractmethod
    def online(self) -> None:
        ...

    def periodic(self) -> None:
        return self._state()
