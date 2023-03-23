import logging
import os
from typing import Optional

from ddtrace.internal import agent
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.remoteconfig.worker import RemoteConfigWorker
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


class RemoteConfig(object):
    _worker = None
    _worker_lock = forksafe.Lock()

    @classmethod
    def _check_remote_config_enable_in_agent(cls):
        # type: () -> Optional[bool]
        data = agent._healthcheck()
        if data and data.get("endpoints"):
            if REMOTE_CONFIG_AGENT_ENDPOINT in data.get("endpoints", []) or (
                "/" + REMOTE_CONFIG_AGENT_ENDPOINT
            ) in data.get("endpoints", []):
                return True

        if asbool(os.environ.get("DD_TRACE_DEBUG")) or "DD_REMOTE_CONFIGURATION_ENABLED" in os.environ:
            LOG_LEVEL = logging.WARNING
        else:
            LOG_LEVEL = logging.DEBUG

        log.log(
            LOG_LEVEL,
            "Agent is down or Remote Config is not enabled in the Agent\n"
            "Check your Agent version, you need an Agent running on 7.39.1 version or above.\n"
            "Check Your Remote Config environment variables on your Agent:\n"
            "DD_REMOTE_CONFIGURATION_ENABLED=true\n"
            "See: https://docs.datadoghq.com/agent/guide/how_remote_config_works/",
        )
        return False

    @classmethod
    def enable(cls):
        # type: () -> bool
        # TODO: this is only temporary. DD_REMOTE_CONFIGURATION_ENABLED variable will be deprecated
        rc_env_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true"))
        if rc_env_enabled and cls._check_remote_config_enable_in_agent():
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
    def disable(cls):
        # type: () -> None
        with cls._worker_lock:
            if cls._worker is not None:
                cls._worker.stop()
                cls._worker = None

                forksafe.unregister(cls._restart)
                atexit.unregister(cls.disable)
