from datetime import datetime
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.handler import _generate_tracer_flare
from ddtrace.internal.flare.handler import _prepare_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


def _is_flare_config(md):
    # Accept AGENT_CONFIG if it has a config dict with a log_level key
    config = md.content
    log.warning("JJJ Checking flare config with content: %r", config)
    if isinstance(config, list) and len(config) == 2:
        config_data = config[1]
        if isinstance(config_data, dict):
            config_dict = config_data.get("config", {})
            log.warning("JJJ Config dict: %r", config_dict)
            if isinstance(config_dict, dict) and "log_level" in config_dict:
                return True
    return False


def _is_flare_task(md):
    # Check if AGENT_TASK is a flare task (task_type == 'tracer_flare')
    config = md.content
    log.warning("JJJ Checking flare task with content: %r", config)
    if isinstance(config, list) and len(config) == 2:
        config_data = config[1]
        if isinstance(config_data, dict):
            task_type = config_data.get("task_type")
            log.warning("JJJ Task type: %r", task_type)
            return task_type == "tracer_flare"
    return False


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        callback: Callable,
        flare: Flare,
        stale_flare_age: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ):
        log.warning("JJJ TracerFlareSubscriber.__init__()")
        super().__init__(data_connector, callback, "TracerFlareConfig")
        self.current_request_start: Optional[datetime] = None
        self.stale_tracer_flare_num_mins = stale_flare_age
        self.flare = flare
        log.warning("JJJ TracerFlareSubscriber initialized")

    def start(self):
        log.warning("JJJ TracerFlareSubscriber.start()")
        super().start()
        log.warning("JJJ TracerFlareSubscriber.start() end")

    def has_stale_flare(self) -> bool:
        if self.current_request_start:
            curr = datetime.now()
            flare_age = (curr - self.current_request_start).total_seconds()
            stale_age = self.stale_tracer_flare_num_mins * 60
            is_stale = flare_age >= stale_age
            log.warning("JJJ Checking stale flare: age=%s, stale_age=%s, is_stale=%s", flare_age, stale_age, is_stale)
            return is_stale
        return False

    def _get_data_from_connector_and_exec(self, _=None):
        log.warning("JJJ TracerFlareSubscriber._get_data_from_connector_and_exec()")
        if self.has_stale_flare():
            log.info(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self.current_request_start,
            )
            self.current_request_start = None
            self._callback(self.flare, {}, True)
            return

        data = self._data_connector.read()
        if not data:
            log.debug("No data received from data connector")
            return

        log.warning("JJJ Received data from connector: %r", data)
        for md in data:
            product_type = md.metadata.product_name
            log.warning("JJJ Processing product type: %s with content: %r", product_type, md.content)
            # Only process flare configs
            if product_type == "AGENT_CONFIG":
                # Check for flare config
                if not _is_flare_config(md):
                    log.debug("Skipping non-flare AGENT_CONFIG: %r", md.metadata)
                    continue
                # We will only process one tracer flare request at a time
                if self.current_request_start is not None:
                    log.warning(
                        "There is already a tracer flare job started at %s. Skipping new request.",
                        str(self.current_request_start),
                    )
                    continue
                log.info("Preparing tracer flare")
                if _prepare_tracer_flare(self.flare, md.content):
                    self.current_request_start = datetime.now()
            elif product_type == "AGENT_TASK":
                # Check for flare task
                if not _is_flare_task(md):
                    log.debug("Skipping non-flare AGENT_TASK: %r", md.metadata)
                    continue
                # Possible edge case where we don't have an existing flare request
                # In this case we won't have anything to send, so we log and do nothing
                if self.current_request_start is None:
                    log.warning("There is no tracer flare job to complete. Skipping new request.")
                    continue
                log.info("Generating and sending tracer flare")
                if _generate_tracer_flare(self.flare, md.content):
                    self.current_request_start = None
            else:
                log.warning("Received unexpected product type for tracer flare: {}", product_type)
        log.warning("JJJ TracerFlareSubscriber._get_data_from_connector_and_exec() end")
