import logging
import os

from ddtrace.internal import agent
from ddtrace.internal import periodic
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.time import StopWatch
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)


DEFAULT_REMOTECONFIG_POLL_SECONDS = 5.0  # seconds


def get_poll_interval_seconds():
    # type:() -> float
    if os.getenv("DD_REMOTECONFIG_POLL_SECONDS"):
        deprecate(
            "Using environment variable 'DD_REMOTECONFIG_POLL_SECONDS' is deprecated",
            message="Please use DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS instead.",
            removal_version="2.0.0",
        )
    return float(
        os.getenv(
            "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS",
            default=os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS),
        )
    )


class RemoteConfigWorker(periodic.PeriodicService):
    """Remote configuration worker.

    This implements a finite-state machine that allows checking the agent for
    the expected endpoint, which could be enabled after the client is started.
    """

    def __init__(self):
        super(RemoteConfigWorker, self).__init__(interval=get_poll_interval_seconds())
        self._client = RemoteConfigClient()
        log.debug("RemoteConfigWorker created with polling interval %d", get_poll_interval_seconds())
        self._state = self._agent_check

    def _agent_check(self):
        # type: () -> None
        try:
            info = agent.info()
        except Exception:
            info = None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and (
                REMOTE_CONFIG_AGENT_ENDPOINT in endpoints or ("/" + REMOTE_CONFIG_AGENT_ENDPOINT) in endpoints
            ):
                self._state = self._online
                return

        if asbool(os.environ.get("DD_TRACE_DEBUG")) or "DD_REMOTE_CONFIGURATION_ENABLED" in os.environ:
            LOG_LEVEL = logging.WARNING
        else:
            LOG_LEVEL = logging.DEBUG

        log.log(
            LOG_LEVEL,
            "Agent is down or Remote Config is not enabled in the Agent\n"
            "Check your Agent version, you need an Agent running on 7.39.1 version or above.\n"
            "Check Your Remote Config environment variables on your Agent:\n"
            "DD_REMOTE_CONFIGURATION_ENABLED=true\n"
            "See: https://docs.datadoghq.com/agent/guide/how_remote_config_works/",
        )

    def _online(self):
        # type: () -> None
        with StopWatch() as sw:
            if not self._client.request():
                # An error occurred, so we transition back to the agent check
                self._state = self._agent_check
                return

        elapsed = sw.elapsed()
        if elapsed >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "request config in %.5fs to %s", elapsed, self._client.agent_url)

    def periodic(self):
        # type: () -> None
        return self._state()
