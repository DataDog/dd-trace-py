import abc
import json
import typing as t

from ddtrace.internal.constants import CONTAINER_TAGS_HASH
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.process_tags import compute_base_hash
from ddtrace.internal.settings._agent import config
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter

from .utils.http import get_connection


log = get_logger(__name__)


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


class _RetryableInfoError(Exception):
    """Raised by _info_with_retry on 5xx so the retry decorator triggers another attempt."""

    def __init__(self, status, reason):
        super().__init__("HTTP %s %s" % (status, reason))
        self.status = status
        self.reason = reason


@fibonacci_backoff_with_jitter(
    attempts=3,
    initial_wait=0.2,
    until=lambda result: isinstance(result, tuple),
)
def _info_with_retry():
    """Variant of info() that retries on transient 5xx failures.

    Scoped to AgentCheckPeriodicService so info() callers keep their existing
    single-attempt semantics. Returns (status, reason, data) on a non-5xx
    response; raises _RetryableInfoError on persistent 5xx after all attempts.
    """
    agent_url = config.trace_agent_url
    timeout = config.trace_agent_timeout_seconds
    _conn = get_connection(agent_url, timeout=timeout)
    try:
        _conn.request("GET", "info", headers={"content-type": "application/json"})
        resp = _conn.getresponse()
        process_info_headers(resp)
        data = resp.read()
    finally:
        _conn.close()

    if 500 <= resp.status < 600:
        raise _RetryableInfoError(resp.status, resp.reason)

    return resp.status, resp.reason, data


class AgentCheckPeriodicService(ForksafeAwakeablePeriodicService, metaclass=abc.ABCMeta):
    def __init__(self, interval: float = 0.0):
        super().__init__(interval=interval)

        self._state = self._agent_check

    @abc.abstractmethod
    def info_check(self, agent_info: t.Optional[dict]) -> bool: ...

    def _agent_check(self) -> None:
        agent_info: t.Optional[dict] = None
        try:
            status, _, data = _info_with_retry()
            if 200 <= status < 300:
                agent_info = json.loads(data)
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
    def online(self) -> None: ...

    def periodic(self) -> None:
        return self._state()
