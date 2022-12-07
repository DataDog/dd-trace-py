from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.worker import RemoteConfigWorker


log = get_logger(__name__)


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
        try:
            # By enabling on registration we ensure we start the RCM client only
            # if there is at least one registered product.
            cls.enable()
            cls._worker._client.register_product(product, handler)
        except Exception:
            log.warning("error starting the RCM client", exc_info=True)

    @classmethod
    def disable(cls):
        # type: () -> None
        with cls._worker_lock:
            if cls._worker is not None:
                cls._worker.stop()
                cls._worker = None

                forksafe.unregister(cls._restart)
                atexit.unregister(cls.disable)
