import os

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.worker import RemoteConfigWorker
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


class RemoteConfig(object):
    _worker = None
    _worker_lock = forksafe.Lock()

    @classmethod
    def enable(cls):
        # type: () -> bool
        # TODO: this is only temporary. DD_REMOTE_CONFIGURATION_ENABLED variable will be deprecated
        rc_env_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true"))
        if rc_env_enabled:
            with cls._worker_lock:
                if cls._worker is None:
                    cls._worker = RemoteConfigWorker()
                    cls._worker.start()

                    forksafe.register(cls._restart)
                    atexit.register(cls.disable)
            return True
        return False

    @classmethod
    def _restart(cls):
        cls.disable()
        cls.enable()

    @classmethod
    def register(cls, product, handler):
        try:
            # By enabling on registration we ensure we start the RCM client only
            # if there is at least one registered product.
            if cls.enable():
                cls._worker._client.register_product(product, handler)
        except Exception:
            log.warning("error starting the RCM client", exc_info=True)

    @classmethod
    def unregister(cls, product):
        try:
            cls._worker._client.unregister_product(product)
        except Exception:
            log.warning("error starting the RCM client", exc_info=True)

    @classmethod
    def disable(cls, join=False):
        # type: (bool) -> None
        with cls._worker_lock:
            if cls._worker is not None:
                cls._worker.stop()
                if join:
                    cls._worker.join()
                cls._worker = None

                forksafe.unregister(cls._restart)
                atexit.unregister(cls.disable)

    def __enter__(self):
        # type: () -> RemoteConfig
        self.enable()
        return self

    def __exit__(self, *args):
        # type: (...) -> None
        self.disable(join=True)
