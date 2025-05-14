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


class TracerFlareSubscriber(RemoteConfigSubscriber):
    def __init__(
        self,
        data_connector: PublisherSubscriberConnector,
        callback: Callable,
        flare: Flare,
        stale_flare_age: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ):
        super().__init__(data_connector, callback, "TracerFlareConfig")
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
        stale = self.has_stale_flare()
        log.debug(
            "[TRACER_FLARE] Executing data connector read: stale_flare=%s, current_request_start=%s",
            stale,
            self.current_request_start,
        )

        if stale:
            log.info(
                "[TRACER_FLARE] Stale flare request detected. Started at %s, stale duration: %d mins",
                self.current_request_start,
                self.stale_tracer_flare_num_mins,
            )
            self.current_request_start = None
            self._callback(self.flare, {}, True)
            return

        data = self._data_connector.read()
        log.debug("[TRACER_FLARE] Data connector read result: data_received=%r", data)

        if not data:
            return

        for md in data:
            product_type = md.metadata.product_name
            log.debug(
                "[TRACER_FLARE] Processing metadata: product_type=%s, content=%r",
                product_type,
                md.content if hasattr(md, "content") else -1,
            )

            if product_type == "AGENT_CONFIG":
                if self.current_request_start is not None:
                    log.warning(
                        "[TRACER_FLARE] Existing tracer flare job in progress. "
                        "Current request started at %s. Skipping new request.",
                        str(self.current_request_start),
                    )
                    continue

                log.info("Preparing tracer flare")
                if _prepare_tracer_flare(self.flare, [md.content]):
                    self.current_request_start = datetime.now()
                    log.debug("[TRACER_FLARE] Tracer flare preparation started at %s", self.current_request_start)

            elif product_type == "AGENT_TASK":
                if self.current_request_start is None:
                    log.warning("[TRACER_FLARE] No active tracer flare job to complete. Skipping AGENT_TASK request.")
                    continue

                log.info("Generating and sending tracer flare")
                if _generate_tracer_flare(self.flare, [md.content]):
                    self.current_request_start = None
                    log.debug("[TRACER_FLARE] Tracer flare task completed")

            else:
                log.warning("[TRACER_FLARE] Unexpected product type: %s. Full metadata: %r", product_type, md)
