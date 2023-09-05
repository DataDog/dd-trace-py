import os
import time
from typing import TYPE_CHECKING

from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicThread
from ddtrace.internal.remoteconfig.utils import get_poll_interval_seconds


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable
    from typing import Optional

    from ddtrace import Tracer
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
    from ddtrace.internal.remoteconfig._connectors import SharedDataType


log = get_logger(__name__)


class RemoteConfigSubscriber(object):
    _th_worker = None

    def __init__(self, data_connector, callback, name):
        # type: (PublisherSubscriberConnector, Callable, str) -> None
        self._data_connector = data_connector
        self.is_running = False
        self._callback = callback
        self._name = name
        log.debug("[%s] Subscriber %s init", os.getpid(), self._name)
        self.interval = get_poll_interval_seconds() / 2

    def _exec_callback(self, data, test_tracer=None):
        # type: (SharedDataType, Optional[Tracer]) -> None
        if data:
            log.debug("[%s] Subscriber %s _exec_callback: %s", os.getpid(), self._name, str(data)[:50])
            self._callback(data, test_tracer=test_tracer)

    def _get_data_from_connector_and_exec(self, test_tracer=None):
        # type: (Optional[Tracer]) -> None
        data = self._data_connector.read()
        self._exec_callback(data, test_tracer=test_tracer)

    def _worker(self):
        self.is_running = True
        while self.is_running:
            try:
                self._get_data_from_connector_and_exec()
            except Exception:
                log.debug(
                    "[%s][P: %s] Subscriber %s get an error", os.getpid(), os.getppid(), self._name, exc_info=True
                )
            time.sleep(self.interval)

    def start(self):
        log.debug("[%s][P: %s] Subscriber %s starts %s", os.getpid(), os.getppid(), self._name, self.is_running)
        if not self.is_running:
            self._th_worker = PeriodicThread(
                target=self._worker,
                interval=self.interval,
                on_shutdown=self.stop,
                name="%s:%s" % (self.__class__.__module__, self.__class__.__name__),
            )
            self._th_worker.start()

    def force_restart(self):
        self.is_running = False
        log.debug(
            "[%s][P: %s] Subscriber %s worker restarts. Status: %s",
            os.getpid(),
            os.getppid(),
            self._name,
            self.is_running,
        )
        self.start()

    def stop(self, join=False):
        # type: (bool) -> None
        if self._th_worker:
            self.is_running = False
            self._th_worker.stop()
            if join:
                self._th_worker.join()
            log.debug("[%s][P: %s] Subscriber %s. Stopped", os.getpid(), os.getppid(), self._name)
