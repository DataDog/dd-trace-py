import abc
import os
import threading
import time

import six

from ddtrace.internal.logger import get_logger

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
        super().__init__(data_connector, callback, name)

    def _exec_callback(self, data):
        if data:
            log.debug("[%s] Subscriber %s _exec_callback", os.getpid(), self._name)
            self._callback(data)

    def _worker(self):
        self.running = True
        checksum = 0
        while self.running:
            try:
                data = self._data_connector.read()
                if data:
                    data_raw = str(data)
                    last_checksum = hash(data_raw)
                    if last_checksum != checksum:
                        log.debug(
                            "[%s][P: %s] Listener %s worker ENCODED data: %s",
                            os.getpid(),
                            os.getppid(),
                            self._name,
                            data_raw[:100],
                        )
                        # decoded = base64.b64decode(self.shared_data.value)
                        log.debug(
                            "[%s][P: %s] Listener %s worker get data: %s",
                            os.getpid(),
                            os.getppid(),
                            self._name,
                            data_raw[:100],
                        )
                        checksum = hash(data_raw)
                        self._exec_callback(data)
                    else:
                        log.debug(
                            "[%s][P: %s] Listener %s worker NO NEW DATA: %s",
                            os.getpid(),
                            os.getppid(),
                            self._name,
                            data_raw[:100],
                        )
            except Exception as e:
                log.debug("[%s] Listener %s error", os.getpid(), self._name, exc_info=True)
            time.sleep(1)

    def start(self):
        log.debug("[%s][P: %s] Listener %s start %s", os.getpid(), os.getppid(), self._name, self.running)
        if not self.running:
            self._th_worker = threading.Thread(target=self._worker)
            self._th_worker.start()

    def force_restart(self):
        self.running = False
        log.debug("[%s][P: %s] Listener %s force_restart %s", os.getpid(), os.getppid(), self._name, self.running)
        self.start()

    def stop(self):
        self.running = False
        if self._th_worker:
            pass  # self._th_worker.join()
