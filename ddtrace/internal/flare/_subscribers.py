from datetime import datetime
import os
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        callback: Callable,
        stale_flare_age: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ):
        super().__init__(data_connector, callback, "TracerFlareConfig")
        self.current_request_start: Optional[datetime] = None
        self.stale_tracer_flare_num_mins = stale_flare_age

    def has_stale_flare(self) -> bool:
        if self.current_request_start:
            curr = datetime.now()
            flare_age = (curr - self.current_request_start).total_seconds()
            stale_age = self.stale_tracer_flare_num_mins * 60
            return flare_age >= stale_age
        return False

    def _get_data_from_connector_and_exec(self):
        if self.current_request_start is not None and self.has_stale_flare():
            log.info(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self.current_request_start,
            )
            self.current_request_start = None
            self._callback({}, True)
            return

        data = self._data_connector.read()
        product_type = data.get("metadata", [{}])[0].get("product_name")
        if product_type == "AGENT_CONFIG":
            # We will only process one tracer flare request at a time
            if self.current_request_start is not None:
                log.warning(
                    "There is already a tracer flare job started at %s. Skipping new request.",
                    str(self.current_request_start),
                )
                return
            self.current_request_start = datetime.now()
        elif product_type == "AGENT_TASK":
            # Possible edge case where we don't have an existing flare request
            # In this case we won't have anything to send, so we log and do nothing
            if self.current_request_start is None:
                log.warning("There is no tracer flare job to complete. Skipping new request.")
                return
            self.current_request_start = None
        else:
            return
        log.debug("[PID %d] %s _exec_callback: %s", os.getpid(), self, str(data)[:50])
        self._callback(data)
