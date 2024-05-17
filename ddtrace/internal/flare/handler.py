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

        def __init__(self, flare: Flare):
            self._publisher = self.__publisher_class__(self.__shared_data__, None)
            self._subscriber = self.__subscriber_class__(self.__shared_data__, flare)

    return _TracerFlarePubSub


def _clean_up_tracer_flare(flare: Flare) -> bool:
    try:
        flare.revert_configs()
        flare.clean_up_files()
        return True
    except Exception:
        return False


def _prepare_tracer_flare(flare: Flare, data: dict) -> bool:
    """
    Update configurations to start sending tracer logs to a file
    to be sent in a flare later.
    """
    configs = data.get("config", [])
    for c in configs:
        # AGENT_CONFIG is currently being used for multiple purposes
        # We only want to prepare for a tracer flare if the config name
        # starts with 'flare-log-level'
        if not c.get("name", "").startswith("flare-log-level"):
            continue

        flare_log_level = c.get("config", {}).get("log_level").upper()
        flare.prepare(flare_log_level)
        return True
    return False


def _generate_tracer_flare(flare: Flare, data: dict) -> bool:
    """
    Revert tracer flare configurations back to original state
    before sending the flare.
    """
    configs = data.get("config", [])
    for c in configs:
        # AGENT_TASK is currently being used for multiple purposes
        # We only want to generate the tracer flare if the task_type is
        # 'tracer_flare'
        if type(c) != dict or c.get("task_type") != "tracer_flare":
            continue
        args = c.get("args", {})
        flare_request = FlareSendRequest(
            case_id=args.get("case_id"), hostname=args.get("hostname"), email=args.get("user_handle")
        )
        flare.revert_configs()
        flare.send(
            flare_request,
        )
        return True
    return False
