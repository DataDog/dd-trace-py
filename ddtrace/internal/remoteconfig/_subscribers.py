import os
from typing import Callable
from typing import Sequence

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._connectors import SharedDataType


log = get_logger(__name__)


class RemoteConfigSubscriber(PeriodicService):
    def __init__(
        self, data_connector: PublisherSubscriberConnector, callback: Callable[[Sequence[Payload]], None], name: str
    ) -> None:
        super().__init__(config._remote_config_poll_interval / 2)

        self._data_connector = data_connector
        self._callback = callback
        self._name = name

        log.debug("[PID %d] %s initialized", os.getpid(), self)

    def _exec_callback(self, data: SharedDataType) -> None:
        if data:
            log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
            self._callback(data)

    def _get_data_from_connector_and_exec(self) -> None:
        data = self._data_connector.read()
        self._exec_callback(data)

    def periodic(self):
        try:
            log.debug("[PID %d | PPID %d] %s is getting data", os.getpid(), os.getppid(), self)
            self._get_data_from_connector_and_exec()
            log.debug("[PID %d | PPID %d] %s got data", os.getpid(), os.getppid(), self)
        except Exception:
            log.error("[PID %d | PPID %d] %s while getting data", os.getpid(), os.getppid(), self, exc_info=True)

    def force_restart(self, join=False):
        self.stop()
        if join:
            self.join()
        self.start()
        log.debug("[PID %d | PPID %d] %s restarted", os.getpid(), os.getppid(), self)

    def __str__(self):
        return f"Subscriber {self._name}"
