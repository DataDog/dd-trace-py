from datetime import datetime
from typing import Optional  # noqa:F401

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        flare: Flare,
        stale_flare_age: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ):
        super().__init__(data_connector, lambda _data: None, "TracerFlareConfig")
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

    def _get_data_from_connector_and_exec(self, _=None):
        if self.has_stale_flare():
            log.info(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self.current_request_start,
            )
            self.current_request_start = None
            self.flare.revert_configs()
            self.flare.clean_up_files()
            return

        data = self._data_connector.read()
        if not data:
            log.debug("No data received from data connector")
            return

        for md in data:
            product_type = md.metadata.product_name
            item = md.content
            if not isinstance(item, dict):
                log.debug("Config item is not type dict, received type %s instead. Skipping...", str(type(item)))
                continue
            flare_action = self.flare.handle_remote_config_data(item, product_type)

            if flare_action.is_set():
                # We will only process one tracer flare request at a time
                if self.current_request_start is not None:
                    log.warning(
                        "There is already a tracer flare job started at %s. Skipping new request.",
                        str(self.current_request_start),
                    )
                    continue
                log.info("Preparing tracer flare")

                log_level = flare_action.level
                if log_level is None:
                    log.warning("Received set flare action without log level")
                    continue
                if self.flare.prepare(log_level):
                    self.current_request_start = datetime.now()
            elif flare_action.is_send():
                # Edge case: AGENT_TASK received without prior AGENT_CONFIG
                # Start the flare job now with default settings before sending
                if self.current_request_start is None:
                    log.info("Starting tracer flare job for AGENT_TASK without prior AGENT_CONFIG")
                    # Prepare with default log level (similar to how .NET handles this)
                    if self.flare.prepare("DEBUG"):
                        self.current_request_start = datetime.now()
                    else:
                        log.warning("Failed to prepare tracer flare. Skipping new request.")
                        continue

                log.info("Generating and sending tracer flare")

                self.flare.revert_configs()
                self.flare.send(flare_action)
                self.current_request_start = None
            else:
                log.warning("Received unexpected product type for tracer flare: {}", product_type)
