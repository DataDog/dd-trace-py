import atexit
import logging
import os

from ddtrace.internal import forksafe
from ddtrace.internal import periodic
from ddtrace.internal.remoteconfig._client import Client
from ddtrace.internal.utils.time import StopWatch


log = logging.getLogger(__name__)


DEFAULT_REMOTECONFIG_POLL_SECONDS = 2.0  # seconds


def get_poll_interval_seconds():
    # type:() -> int
    return int(os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS))


class RemoteConfigWorker(periodic.PeriodicService):
    def __init__(self, poll_interval=get_poll_interval_seconds()):
        super(RemoteConfigWorker, self).__init__(interval=poll_interval)
        self._client = Client()

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


class RemoteConfig(object):
    _worker = None
    _worker_lock = forksafe.Lock()

    @classmethod
    def enable(cls):
        # type: () -> None
        with cls._worker_lock:
            if cls._worker is None:
                cls._worker = RemoteConfigWorker()
                cls._worker.start()

                forksafe.register(cls._restart)
                atexit.register(cls.disable)

    @classmethod
    def _restart(cls):
        cls.disable()
        cls.enable()

    @classmethod
    def register(cls, product, handler):
        if cls._worker is not None:
            cls._worker._client.register_product(product, handler)

    @classmethod
    def disable(cls):
        # type: () -> None
        with cls._worker_lock:
            if cls._worker is not None:
                cls._worker.stop()
                cls._worker = None

                forksafe.unregister(cls._restart)
                atexit.unregister(cls.disable)
