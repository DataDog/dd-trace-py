import abc
import json
import typing as t

from ddtrace.internal.constants import CONTAINER_TAGS_HASH
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.process_tags import compute_base_hash
from ddtrace.internal.settings._agent import config
from ddtrace.internal.utils.time import HourGlass

from .utils.http import get_connection


log = get_logger(__name__)

# Bound how often /info is polled while the agent is unreachable or unsupported.
AGENT_CHECK_INTERVAL = 60.0


def process_info_headers(resp):
    try:
        container_tags_hash = resp.getheader(CONTAINER_TAGS_HASH)
        if container_tags_hash:
            compute_base_hash(container_tags_hash)
    except Exception as e:
        log.debug("Could not compute base hash: %s", e)


def info(url=None):
    agent_url = config.trace_agent_url if url is None else url
    timeout = config.trace_agent_timeout_seconds
    _conn = get_connection(agent_url, timeout=timeout)
    try:
        _conn.request("GET", "info", headers={"content-type": "application/json"})
        resp = _conn.getresponse()
        process_info_headers(resp)
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
        self._agent_check_throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)

    @abc.abstractmethod
    def info_check(self, agent_info: t.Optional[dict]) -> bool: ...

    def _throttle_agent_check(self) -> None:
        """Back off from checking the agent until the throttle window elapses.

        Call this only for a confirmed unsupported agent (reachable, but not
        advertising the required endpoints). Transient ``/info`` failures and
        upload failures on a supported agent are left to recover on the next
        tick rather than being suppressed for ``AGENT_CHECK_INTERVAL``.
        """
        self._agent_check_throttle.turn()

    def _agent_check(self) -> None:
        if self._agent_check_throttle.trickling():
            return

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

    def reset(self) -> None:
        # On fork, re-check the agent immediately rather than inheriting the
        # parent's throttle window.
        super().reset()
        self._agent_check_throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)

    @abc.abstractmethod
    def online(self) -> None: ...

    def periodic(self) -> None:
        return self._state()
