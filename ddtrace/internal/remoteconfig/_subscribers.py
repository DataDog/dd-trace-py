import os
from typing import Callable
from typing import Sequence

from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector


log = get_logger(__name__)


class RemoteConfigSubscriber(PeriodicService):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        callback: Callable[[Sequence[Payload]], None],
        name: str,
        lock=None,
    ) -> None:
        super().__init__(config._remote_config_poll_interval / 2)

        self._data_connector = data_connector
        self._callback = callback
        self._name = name
        # Shared with the polling process' synchronous pump to serialize read + dispatch
        self._lock = lock or forksafe.Lock()

        log.debug("[PID %d] %s initialized", os.getpid(), self)

    def periodic(self):
        try:
            with self._lock:
                # Read data from connector
                data = self._data_connector.read()
                self._callback(data)
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
