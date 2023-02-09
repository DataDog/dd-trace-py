import logging
import os
from typing import Optional

from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import periodic
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.service import ServiceStatus
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


class RemoteConfigPoller(periodic.PeriodicService):
    def __init__(self):
        super(RemoteConfigPoller, self).__init__(interval=get_poll_interval_seconds())
        self._client = RemoteConfigClient()
        log.debug("RemoteConfigWorker created with polling interval %d", get_poll_interval_seconds())

    def periodic(self):
        # type: () -> None
        with StopWatch() as sw:
            if self._client.has_products():
                self._client.request()

        t = sw.elapsed()
        if t >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "request config in %.5fs to %s", t, self._client.agent_url)

    @staticmethod
    def _check_remote_config_enable_in_agent():
        # type: () -> Optional[bool]
        data = agent._healthcheck()
        if data and data.get("endpoints"):
            if REMOTE_CONFIG_AGENT_ENDPOINT in data.get("endpoints", []) or (
                "/" + REMOTE_CONFIG_AGENT_ENDPOINT
            ) in data.get("endpoints", []):
                return True
        log.warning(
            "Agent is down or Remote Config is not enabled in the Agent\n"
            "Check your Agent version, you need an Agent running on 7.39.1 version or above.\n"
            "Check Your Remote Config environment variables on your Agent:\n"
            "DD_REMOTE_CONFIGURATION_ENABLED=true\n"
            "DD_REMOTE_CONFIGURATION_KEY=<YOUR-KEY>\n"
            "See: https://app.datadoghq.com/organization-settings/remote-config"
        )
        return False

    def enable(self):
        # type: () -> None
        """
        Enable the instrumentation telemetry collection service. If the service has already been
        activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
        """
        if self.status == ServiceStatus.RUNNING:
            return

        if self._check_remote_config_enable_in_agent():
            self.start()

            atexit.register(self.stop)

    def disable(self):
        # type: () -> None
        """
        Disable the remote config service and drop, remote config can be re-enabled
        by calling ``enable`` again.
        """
        if self.status == ServiceStatus.STOPPED:
            return

        atexit.unregister(self.stop)

        self.stop()

    def shutdown(self, timeout):
        # type: (Optional[float]) -> None
        self.stop(timeout)

    def _stop_service(
        self,
        timeout=None,  # type: Optional[float]
    ):
        # type: (...) -> None
        super(RemoteConfigPoller, self)._stop_service()
        self.join(timeout=timeout)

    def register(self, product, handler):
        try:
            self._client.register_product(product, handler)
        except Exception:
            log.warning("error starting the RCM client", exc_info=True)

    def unregister(self, product):
        try:
            self._client.unregister_product(product)
        except Exception:
            log.warning("error starting the RCM client", exc_info=True)


remoteconfig_poller = RemoteConfigPoller()
