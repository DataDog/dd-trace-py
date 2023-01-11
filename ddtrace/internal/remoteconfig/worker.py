import logging
import os

from ddtrace.internal import periodic
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.utils.time import StopWatch


log = get_logger(__name__)


DEFAULT_REMOTECONFIG_POLL_SECONDS = 5.0  # seconds


def get_poll_interval_seconds():
    # type:() -> float
    if os.getenv("DD_REMOTECONFIG_POLL_SECONDS"):
        log.warning(
            "DD_REMOTECONFIG_POLL_SECONDS environment variable is deprecated in favor of "
            "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS.\n"
            "DD_REMOTECONFIG_POLL_SECONDS will be removed in next version"
        )
        return float(os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS))
    return float(os.getenv("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS))


class RemoteConfigWorker(periodic.PeriodicService):
    def __init__(self):
        super(RemoteConfigWorker, self).__init__(interval=get_poll_interval_seconds())
        self._client = RemoteConfigClient()
        log.debug("RemoteConfigWorker created with polling interval %d", get_poll_interval_seconds())

    def periodic(self):
        # type: () -> None
        with StopWatch() as sw:
            self._client.request()

        t = sw.elapsed()
        if t >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "request config in %.5fs to %s", t, self._client.agent_url)
