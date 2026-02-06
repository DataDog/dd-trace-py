from datetime import datetime
import typing as t
from typing import Callable
from typing import Optional

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.handler import _generate_tracer_flare
from ddtrace.internal.flare.handler import _prepare_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


class TracerFlareState:
    """Shared state between the callback and stale check service."""

    def __init__(self):
        self.current_request_start: Optional[datetime] = None


class TracerFlareCallback(RCCallback):
    """Remote config callback for tracer flare requests.

    The periodic() method performs stale flare checks at every polling operation.
    The __call__() method processes tracer flare payloads when present.
    """

    def __init__(
        self,
        callback: Callable[[Flare, dict, bool], None],
        flare: Flare,
        state: TracerFlareState,
        stale_duration_mins: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ) -> None:
        """Initialize the tracer flare callback.

        Args:
            callback: Handler function for flare operations
            flare: Flare instance for generating flares
            state: Shared state for tracking flare requests
            stale_duration_mins: Minutes before a flare request is considered stale
        """
        self._callback = callback
        self._flare = flare
        self._state = state
        self._stale_duration_secs = stale_duration_mins * 60

    def _has_stale_flare(self) -> bool:
        """Check if the current flare request is stale."""
        if self._state.current_request_start:
            curr = datetime.now()
            flare_age = (curr - self._state.current_request_start).total_seconds()
            return flare_age >= self._stale_duration_secs
        return False

    def periodic(self) -> None:
        """Periodic method called at every polling operation.

        Checks for stale flare requests and cleans them up if necessary.
        """
        if self._has_stale_flare():
            log.info(
                "Tracer flare request started at %s is stale, reverting "
                "logger configurations and cleaning up resources now",
                self._state.current_request_start,
            )
            self._state.current_request_start = None
            self._callback(self._flare, {}, True)

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process tracer flare configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        if not payloads:
            return

        for payload in payloads:
            if payload.metadata is None:
                log.debug("No metadata for tracer flare payload")
                continue

            product_type = payload.metadata.product_name
            if product_type == "AGENT_CONFIG":
                # We will only process one tracer flare request at a time
                if self._state.current_request_start is not None:
                    log.warning(
                        "There is already a tracer flare job started at %s. Skipping new request.",
                        str(self._state.current_request_start),
                    )
                    continue
                log.info("Preparing tracer flare")
                # Handle both list (unit tests) and dict (system tests) data structures
                config_data = payload.content if isinstance(payload.content, list) else [payload.content]
                if _prepare_tracer_flare(self._flare, config_data):
                    self._state.current_request_start = datetime.now()

            elif product_type == "AGENT_TASK":
                # Possible edge case where we don't have an existing flare request
                # In this case we won't have anything to send, so we log and do nothing
                if self._state.current_request_start is None:
                    # If no AGENT_CONFIG was received, start the flare job now with default settings
                    log.info("Starting tracer flare job for AGENT_TASK without prior AGENT_CONFIG")
                    # Prepare with default log level (similar to how .NET handles this)
                    if self._flare.prepare("DEBUG"):
                        self._state.current_request_start = datetime.now()
                    else:
                        log.warning("Failed to prepare tracer flare. Skipping new request.")
                        continue

                log.info("Generating and sending tracer flare")
                # Handle both list (unit tests) and dict (system tests) data structures
                task_data = payload.content if isinstance(payload.content, list) else [payload.content]
                if _generate_tracer_flare(self._flare, task_data):
                    self._state.current_request_start = None
            else:
                log.warning("Received unexpected product type for tracer flare: %s", product_type)
