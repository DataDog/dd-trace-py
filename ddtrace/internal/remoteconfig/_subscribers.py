import abc
import os
import time

import six

from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicThread
from ddtrace.internal.remoteconfig.utils import get_poll_interval_seconds


log = get_logger(__name__)


class SubscriberBase(six.with_metaclass(abc.ABCMeta)):
    def __init__(self, data_connector, callback, name):
        self._data_connector = data_connector
        self.running = False
        self._callback = callback
        self._name = name
        log.debug("[%s] Subscriber %s init", os.getpid(), self._name)

    @abc.abstractmethod
    def start(self):
        pass

    @abc.abstractmethod
    def stop(self):
        pass

    @abc.abstractmethod
    def force_restart(self):
        pass


class SubscriberMock(SubscriberBase):
    def start(self):
        pass

    def stop(self):
        pass

    def force_restart(self):
        pass


class RemoteConfigSubscriber(SubscriberBase):
    _th_worker = None

    def __init__(self, data_connector, callback, name):
        super(RemoteConfigSubscriber, self).__init__(data_connector, callback, name)

    def _exec_callback(self, data, test_tracer=None):
        log.debug("[%s] Subscriber %s _exec_callback", os.getpid(), self._name)
        if data:
            self._callback(data, test_tracer=test_tracer)

    def _get_data_from_connector_and_exec(self, checksum=0, test_tracer=None):
        data = self._data_connector.read()
        data_raw = str(data)
        last_checksum = hash(data_raw)
        if last_checksum != checksum:
            log.debug(
                "[%s][P: %s] Subscriber %s worker ENCODED data: %s",
                os.getpid(),
                os.getppid(),
                self._name,
                data_raw[:100],
            )
            log.debug(
                "[%s][P: %s] Subscriber %s worker get data: %s",
                os.getpid(),
                os.getppid(),
                self._name,
                data_raw[:100],
            )
            checksum = hash(data_raw)
            self._exec_callback(data, test_tracer=test_tracer)

        return checksum

    def _worker(self):
        self.running = True
        checksum = 0
        while self.running:
            try:
                checksum = self._get_data_from_connector_and_exec(checksum)
            except Exception:
                log.debug("[%s] Subscriber %s error", os.getpid(), self._name, exc_info=True)
            time.sleep(get_poll_interval_seconds())

    def start(self):
        log.debug("[%s][P: %s] Subscriber %s start %s", os.getpid(), os.getppid(), self._name, self.running)
        if not self.running:
            self._th_worker = PeriodicThread(
                target=self._worker, interval=get_poll_interval_seconds(), on_shutdown=self.stop
            )
            self._th_worker.start()

    def force_restart(self):
        self.running = False
        log.debug("[%s][P: %s] Subscriber %s force_restart %s", os.getpid(), os.getppid(), self._name, self.running)
        self.start()

    def stop(self):
        if self._th_worker:
            self.running = False
            log.debug("[%s][P: %s] Subscriber %s. Starting to stop", os.getpid(), os.getppid(), self._name)
            self._th_worker.stop()
            log.debug("[%s][P: %s] Subscriber %s. Stopped", os.getpid(), os.getppid(), self._name)
