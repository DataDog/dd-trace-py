from typing import Any
from typing import Callable
from typing import List

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.flare import FlareSendRequest
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


def _handle_tracer_flare(flare: Flare, data: dict, cleanup: bool = False):
    if cleanup:
        log.info("Reverting tracer flare configurations and cleaning up any generated files")
        flare.revert_configs()
        flare.clean_up_files()
        return

    if "config" not in data:
        log.warning("Unexpected tracer flare RC payload %r", data)
        return
    if len(data["config"]) == 0:
        log.warning("Unexpected number of tracer flare RC payloads %r", data)
        return

    product_type = data.get("metadata", [{}])[0].get("product_name")
    configs = data.get("config", [{}])
    if product_type == "AGENT_CONFIG":
        _prepare_tracer_flare(flare, configs)
    elif product_type == "AGENT_TASK":
        _generate_tracer_flare(flare, configs)
    else:
        log.warning("Received unexpected tracer flare product type: %s", product_type)


def _prepare_tracer_flare(flare: Flare, configs: List[Any]) -> bool:
    """
    Update configurations to start sending tracer logs to a file
    to be sent in a flare later.
    """
    for c in configs:
        # AGENT_CONFIG is currently being used for multiple purposes
        # We only want to prepare for a tracer flare if the config content
        # has a log_level.
        if not isinstance(c, dict):
            log.debug("Config item is not type dict, received type %s instead. Skipping...", str(type(c)))
            continue

        config_content = c.get("config", {})
        log_level = config_content.get("log_level", "")
        if log_level == "":
            log.debug(
                "Config item does not contain log_level, received %s instead. Skipping...",
                config_content.get("log_level"),
            )
            continue

        flare_log_level = log_level.lower()
        flare.prepare(flare_log_level)
        return True
    return False


def _generate_tracer_flare(flare: Flare, configs: List[Any]) -> bool:
    """
    Revert tracer flare configurations back to original state
    before sending the flare.
    """
    for c in configs:
        # AGENT_TASK is currently being used for multiple purposes
        # We only want to generate the tracer flare if the task_type is
        # 'tracer_flare'
        if not isinstance(c, dict):
            log.debug("Config item is not type dict, received type %s instead. Skipping...", str(type(c)))
            continue
        if c.get("task_type") != "tracer_flare":
            log.debug(
                "Config item does not have the expected task_type. Expected [tracer_flare], received [%s]. Skipping...",
                c.get("task_type"),
            )
            continue
        args = c.get("args", {})
        uuid = c.get("uuid")
        if not uuid:
            log.warning("AGENT_TASK config missing UUID, skipping tracer flare")
            continue

        flare_request = FlareSendRequest(
            case_id=args.get("case_id"), hostname=args.get("hostname"), email=args.get("user_handle"), uuid=uuid
        )

        flare.revert_configs()

        flare.send(flare_request)
        return True
    return False
