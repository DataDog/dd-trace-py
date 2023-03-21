import os
from typing import Optional

import six

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
    def enable(cls, client_config=None):
        # type: (Optional[dict]) -> bool
        # TODO: this is only temporary. DD_REMOTE_CONFIGURATION_ENABLED variable will be deprecated
        if client_config:  # JJJ
            from pprint import pformat
            log.warning("JJJ starting rc worker with config:\n%s", pformat(client_config))
        rc_env_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true"))
        if rc_env_enabled:
            with cls._worker_lock:
                if cls._worker is None:
                    cls._worker = RemoteConfigWorker()
                    if client_config:
                        for k, v in six.iteritems(client_config):
                            setattr(cls._worker._client, k, v)
                    cls._worker.start()
                    cls._worker._client.request()  # JJJ

                    forksafe.register(cls._restart)
                    atexit.register(cls.disable)
            return True
        return False

    @classmethod
    def _restart(cls):
        client_config = cls.disable()
        from pprint import pformat
        log.warning("JJJ client_config in restart: %s", pformat(client_config))
        cls.enable(client_config)

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
        # type: (bool) -> Optional[dict]
        client_config = None

        with cls._worker_lock:
            if cls._worker is not None:
                client_config = cls._worker.get_client_config()
                cls._worker.stop()
                if join:
                    cls._worker.join()
                cls._worker = None

                forksafe.unregister(cls._restart)
                atexit.unregister(cls.disable)

        return client_config

    def __enter__(self):
        # type: () -> RemoteConfig
        self.enable()
        return self

    def __exit__(self, *args):
        # type: (...) -> None
        self.disable(join=True)
