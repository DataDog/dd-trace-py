from datetime import datetime
import os
from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.remoteconfig.utils import get_poll_interval_seconds


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace import Tracer  # noqa:F401
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
    from ddtrace.internal.remoteconfig._connectors import SharedDataType  # noqa:F401


log = get_logger(__name__)


class RemoteConfigSubscriber(PeriodicService):
    def __init__(self, data_connector, callback, name):
        # type: (PublisherSubscriberConnector, Callable, str) -> None
        super().__init__(get_poll_interval_seconds() / 2)

        self._data_connector = data_connector
        self._callback = callback
        self._name = name

        log.debug("[PID %d] %s initialized", os.getpid(), self)

    def _exec_callback(self, data, test_tracer=None):
        # type: (SharedDataType, Optional[Tracer]) -> None
        if data:
            log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
            self._callback(data, test_tracer=test_tracer)

    def _get_data_from_connector_and_exec(self, test_tracer=None):
        # type: (Optional[Tracer]) -> None
        data = self._data_connector.read()
        self._exec_callback(data, test_tracer=test_tracer)

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


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(self, data_connector: PublisherSubscriberConnector, callback: Callable):
        super().__init__(data_connector, callback, "TracerFlareConfig")
        self._current_request_start: Optional[datetime] = None

    def _get_data_from_connector_and_exec(self):
        data = self._data_connector.read()
        curr = datetime.now()
        if self._current_request_start is not None:
            delta = curr - self._current_request_start
            if delta.total_seconds >= 20 * 60:
                self._current_request_start = None
                self._callback({}, True)
                return
        product_type = data.get("metadata", [])[0].get("product_name")
        if product_type == "AGENT_CONFIG":
            if self._current_request_start is not None:
                log.warning(
                    "There is already a tracer flare job started at %s. Skipping new request.",
                    str(self._current_request_start),
                )
                return
            self._current_request_start = curr
        elif product_type == "AGENT_TASK":
            if self._current_request_start is None:
                log.warning("There is no tracer flare job to complete. Skipping new request.")
                return
            self._current_request_start = None
        log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
        self._callback(data)

    def periodic(self):
        try:
            log.debug("[PID %d | PPID %d] %s is getting data", os.getpid(), os.getppid(), self)
            self._get_data_from_connector_and_exec()
            log.debug("[PID %d | PPID %d] %s got data", os.getpid(), os.getppid(), self)
        except Exception:
            log.error("[PID %d | PPID %d] %s while getting data", os.getpid(), os.getppid(), self, exc_info=True)
