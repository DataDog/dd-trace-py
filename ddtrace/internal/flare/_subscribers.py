from datetime import datetime
from typing import Optional  # noqa:F401

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.handler import _clean_up_tracer_flare
from ddtrace.internal.flare.handler import _generate_tracer_flare
from ddtrace.internal.flare.handler import _prepare_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


def dummy():
    pass


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        flare: Flare,
        stale_flare_age: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ):
        super().__init__(data_connector, dummy, "TracerFlareConfig")
        self.current_request_start: Optional[datetime] = None
        self.stale_tracer_flare_num_mins = stale_flare_age
        self.flare = flare

    def has_stale_flare(self) -> bool:
        if self.current_request_start:
            curr = datetime.now()
            flare_age = (curr - self.current_request_start).total_seconds()
            stale_age = self.stale_tracer_flare_num_mins * 60
            return flare_age >= stale_age
        return False

    def _get_data_from_connector_and_exec(self):
        if self.has_stale_flare():
            log.info(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self.current_request_start,
            )
            if _clean_up_tracer_flare(self.flare):
                self.current_request_start = None
            return

        data = self._data_connector.read()
        metadata = data.get("metadata")
        if "config" not in data:
            log.warning("Unexpected tracer flare RC payload %r", data)
            return
        if len(data["config"]) == 0:
            log.warning("Unexpected number of tracer flare RC payloads %r", data)
            return
        if not metadata:
            log.debug("No metadata received from data connector")
            return

        for md in metadata:
            product_type = md.get("product_name")
            if product_type == "AGENT_CONFIG":
                # We will only process one tracer flare request at a time
                if self.current_request_start is not None:
                    log.warning(
                        "There is already a tracer flare job started at %s. Skipping new request.",
                        str(self.current_request_start),
                    )
                    return
                timestamp = datetime.now()
                if _prepare_tracer_flare(self.flare, data):
                    self.current_request_start = timestamp
            elif product_type == "AGENT_TASK":
                # Possible edge case where we don't have an existing flare request
                # In this case we won't have anything to send, so we log and do nothing
                if self.current_request_start is None:
                    log.warning("There is no tracer flare job to complete. Skipping new request.")
                    return
                if _generate_tracer_flare(self.flare, data):
                    self.current_request_start = None
            else:
                log.debug("Received unexpected product type for tracer flare: {}", product_type)
                return
            return
