from typing import Any
from typing import Callable
from typing import List

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.flare import FlareSendRequest
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import PayloadType


log = get_logger(__name__)


def _tracerFlarePubSub():
    log.warning("JJJ in _tracerFlarePubSub")
    from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
    from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    class _TracerFlarePubSub(PubSub):
        __publisher_class__ = RemoteConfigPublisher
        __subscriber_class__ = TracerFlareSubscriber
        __shared_data__ = PublisherSubscriberConnector()

        def __init__(self, callback: Callable, flare: Flare):
            log.warning("JJJ in _tracerFlarePubSub._TracerFlarePubSub.__init__()")
            self._publisher = self.__publisher_class__(self.__shared_data__, None)
            self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, flare)
            log.warning("JJJ in _tracerFlarePubSub._TracerFlarePubSub.__init__() end")

    log.warning("JJJ in _tracerFlarePubSub() end")
    return _TracerFlarePubSub


def _handle_tracer_flare(flare: Flare, data: dict, cleanup: bool = False):
    log.warning("JJJ in _handle_tracer_flare with data: %r", data)
    if cleanup:
        log.info("Reverting tracer flare configurations and cleaning up any generated files")
        flare.revert_configs()
        flare.clean_up_files()
        log.warning("JJJ in _handle_tracer_flare end1")
        return

    if "config" not in data:
        log.warning("Unexpected tracer flare RC payload %r", data)
        log.warning("JJJ in _handle_tracer_flare end2")
        return
    if len(data["config"]) == 0:
        log.warning("Unexpected number of tracer flare RC payloads %r", data)
        log.warning("JJJ in _handle_tracer_flare end3")
        return

    product_type = data.get("metadata", [{}])[0].get("product_name")
    configs = data.get("config", [{}])
    log.warning("JJJ Processing product_type: %s with configs: %r", product_type, configs)
    if product_type == "AGENT_CONFIG":
        _prepare_tracer_flare(flare, configs)
    elif product_type == "AGENT_TASK":
        _generate_tracer_flare(flare, configs)
    else:
        log.warning("Received unexpected tracer flare product type: %s", product_type)
    log.warning("JJJ in _handle_tracer_flare end4")


def _prepare_tracer_flare(flare: Flare, config: PayloadType) -> bool:
    """
    Update configurations to start sending tracer logs to a file
    to be sent in a flare later.
    """
    log.warning("JJJ in _prepare_tracer_flare with config: %r", config)
    if not config or not isinstance(config, list) or len(config) != 2:
        log.warning("JJJ in _prepare_tracer_flare end not config or invalid format")
        return False

    config_data = config[1]
    if not isinstance(config_data, dict):
        log.warning("JJJ in _prepare_tracer_flare end config_data not dict")
        return False

    config_dict = config_data.get("config", {})
    log.warning("JJJ Config dict: %r", config_dict)
    if not isinstance(config_dict, dict) or "log_level" not in config_dict:
        log.debug(
            "Config item does not have a log_level in config dict, received [%r] instead. Skipping...",
            config_dict,
        )
        log.warning("JJJ in _prepare_tracer_flare end no log_level in config_dict")
        return False

    flare_log_level = config_dict["log_level"].upper()
    log.warning("JJJ Flare log level: %r", flare_log_level)
    if not flare_log_level:
        log.warning("JJJ in _prepare_tracer_flare end no log_level in config")
        return False

    flare.prepare(flare_log_level)
    log.warning("JJJ in _prepare_tracer_flare end OK True")
    return True


def _generate_tracer_flare(flare: Flare, config: PayloadType) -> bool:
    """
    Revert tracer flare configurations back to original state
    before sending the flare.
    """
    log.warning("JJJ in _generate_tracer_flare with config: %r", config)
    # AGENT_TASK is currently being used for multiple purposes
    # We only want to generate the tracer flare if the task_type is
    # 'tracer_flare'
    if not config or not isinstance(config, list) or len(config) != 2:
        log.warning("JJJ in _generate_tracer_flare end not config or invalid format")
        return False

    # The config is a list with [enabled, config_data]
    config_data = config[1]
    if not isinstance(config_data, dict):
        log.warning("JJJ in _generate_tracer_flare end config_data not dict")
        return False

    task_type = config_data.get("task_type")
    log.warning("JJJ Task type: %r", task_type)
    if task_type != "tracer_flare":
        log.debug(
            "Config item does not have the expected task_type. Expected [tracer_flare], received [%r]. Skipping...",
            task_type,
        )
        log.warning("JJJ in _generate_tracer_flare end not task_type")
        return False

    args = config_data.get("args", {})
    log.warning("JJJ Args: %r", args)
    if not args:
        log.warning("JJJ in _generate_tracer_flare end no args in config")
        return False

    flare_request = FlareSendRequest(
        case_id=args.get("case_id"), hostname=args.get("hostname"), email=args.get("user_handle")
    )

    flare.revert_configs()
    flare.send(flare_request)
    log.warning("JJJ in _generate_tracer_flare end OK True")
    return True
