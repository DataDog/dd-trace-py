from typing import Callable

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _tracerFlarePubSub():
    from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
    from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    class _TracerFlarePubSub(PubSub):
        __publisher_class__ = RemoteConfigPublisher
        __subscriber_class__ = TracerFlareSubscriber
        __shared_data__ = PublisherSubscriberConnector()

        def __init__(self, callback: Callable, flare: Flare):
            self._publisher = self.__publisher_class__(self.__shared_data__, None)
            self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, flare)

    return _TracerFlarePubSub


def _handle_tracer_flare(flare: Flare, data: dict):
    if "config" not in data:
        log.warning("Unexpected tracer flare RC payload %r", data)
        return
    if len(data["config"]) == 0:
        log.warning("Unexpected number of tracer flare RC payloads %r", data)
        return

    product_type = data.get("metadata", [{}])[0].get("product_name")
    configs = data.get("config", [{}])
    for c in configs:
        if not isinstance(c, dict):
            log.debug("Config item is not type dict, received type %s instead. Skipping...", str(type(c)))
            continue
        config_data = c.get("config", {})
        flare_action = flare.native_manager.handle_remote_config_data(config_data, product_type)

        if flare_action.is_send():
            flare.revert_configs()
            flare.send(flare_action)

        elif flare_action.is_set():
            log_level = flare_action.level
            flare.prepare(log_level)
        elif flare_action.is_unset():
            log.info("Reverting tracer flare configurations and cleaning up any generated files")
            flare.revert_configs()
            flare.clean_up_files()
