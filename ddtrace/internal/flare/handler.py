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
    log.debug("[TRACER_FLARE] Handling tracer flare: cleanup=%s, data_keys=%s", cleanup, list(data.keys()))

    if cleanup:
        log.info("[TRACER_FLARE] Reverting tracer flare configurations and cleaning up. Flare object: %s", flare)
        flare.revert_configs()
        flare.clean_up_files()
        return

    if "config" not in data:
        log.warning(
            "[TRACER_FLARE] Unexpected tracer flare RC payload. Received data: %r, keys: %s", data, list(data.keys())
        )
        return

    if len(data["config"]) == 0:
        log.warning(
            "[TRACER_FLARE] Unexpected number of tracer flare RC payloads. Config length: %d, Full data: %r",
            len(data["config"]),
            data,
        )
        return

    product_type = data.get("metadata", [{}])[0].get("product_name")
    configs = data.get("config", [{}])
    log.debug("[TRACER_FLARE] Processing tracer flare: product_type=%s, num_configs=%d", product_type, len(configs))

    if product_type == "AGENT_CONFIG":
        success = _prepare_tracer_flare(flare, configs)
        log.debug("[TRACER_FLARE] Prepare tracer flare result: success=%s", success)
    elif product_type == "AGENT_TASK":
        success = _generate_tracer_flare(flare, configs)
        log.debug("[TRACER_FLARE] Generate tracer flare result: success=%s", success)
    else:
        log.warning(
            "[TRACER_FLARE] Received unexpected tracer flare product type: %s. Full data: %r", product_type, data
        )


def _prepare_tracer_flare(flare: Flare, configs: List[Any]) -> bool:
    """
    Update configurations to start sending tracer logs to a file
    to be sent in a flare later.
    """
    log.debug("[TRACER_FLARE] Preparing tracer flare with %r configs", configs)

    for c in configs:
        log.debug("[TRACER_FLARE] Processing config item: %r", c)
        # AGENT_CONFIG is currently being used for multiple purposes
        # We only want to prepare for a tracer flare if the config name
        # starts with 'flare-log-level'
        if not isinstance(c, dict):
            log.debug(
                "[TRACER_FLARE] Config item is not type dict, received type %s instead. Skipping...", str(type(c))
            )
            continue
        if not c.get("name", "").startswith("flare-log-level"):
            log.debug(
                "[TRACER_FLARE] Config item name does not start with flare-log-level, received %s instead. Skipping...",
                c.get("name"),
            )
            continue

        flare_log_level = c.get("config", {}).get("log_level").upper()
        log.debug("[TRACER_FLARE] Preparing flare with log level %s", flare_log_level)
        flare.prepare(flare_log_level)
        return True
    return False


def _generate_tracer_flare(flare: Flare, configs: List[Any]) -> bool:
    """
    Revert tracer flare configurations back to original state
    before sending the flare.
    """
    log.debug("[TRACER_FLARE] Generating tracer flare with %r configs", configs)

    for c in configs:
        log.debug("[TRACER_FLARE] Processing config item: %r", c)
        # AGENT_TASK is currently being used for multiple purposes
        # We only want to generate the tracer flare if the task_type is
        # 'tracer_flare'
        if not isinstance(c, dict):
            log.debug(
                "[TRACER_FLARE] Config item is not type dict, received type %s instead. Skipping...", str(type(c))
            )
            continue
        if c.get("task_type") != "tracer_flare":
            log.debug(
                "[TRACER_FLARE] Config item does not have the expected task_type. Expected [tracer_flare], received [%s]. Skipping...",
                c.get("task_type"),
            )
            continue
        args = c.get("args", {})
        flare_request = FlareSendRequest(
            case_id=args.get("case_id"), hostname=args.get("hostname"), email=args.get("user_handle")
        )

        log.debug("[TRACER_FLARE] Preparing to send flare for case ID %s", flare_request.case_id)
        flare.revert_configs()

        flare.send(flare_request)
        return True
    return False
