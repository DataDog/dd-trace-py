import os
from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.remoteconfig.utils import get_poll_interval_seconds


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
    from ddtrace.internal.remoteconfig._connectors import SharedDataType  # noqa:F401
    from ddtrace.trace import Tracer  # noqa:F401


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
