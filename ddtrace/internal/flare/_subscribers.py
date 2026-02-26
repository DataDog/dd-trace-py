from datetime import datetime
import typing as t
from typing import Callable
from typing import Optional

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


class TracerFlareState:
    """Shared state between the callback and stale check service."""

    def __init__(self) -> None:
        self.current_request_start: Optional[datetime] = None


def _process_payloads(flare: Flare, state: TracerFlareState, payloads: t.Sequence[Payload]) -> None:
    for payload in payloads:
        if payload.metadata is None:
            log.debug("No metadata for tracer flare payload")
            continue

        product_type = payload.metadata.product_name
        items = payload.content if isinstance(payload.content, list) else [payload.content]
        for item in items:
            if not isinstance(item, dict):
                log.debug("Config item is not type dict, received type %s instead. Skipping...", str(type(item)))
                continue

            flare_action = flare.handle_remote_config_data(item, product_type)
            if flare_action.is_set():
                if state.current_request_start is not None:
                    log.warning(
                        "There is already a tracer flare job started at %s. Skipping new request.",
                        str(state.current_request_start),
                    )
                    continue
                log.info("Preparing tracer flare")
                log_level = flare_action.level
                if log_level is None:
                    log.warning("Received set flare action without log level")
                    continue
                if flare.prepare(log_level):
                    state.current_request_start = datetime.now()

            elif flare_action.is_send():
                if state.current_request_start is None:
                    log.info("Starting tracer flare job for AGENT_TASK without prior AGENT_CONFIG")
                    if flare.prepare("DEBUG"):
                        state.current_request_start = datetime.now()
                    else:
                        log.warning("Failed to prepare tracer flare. Skipping new request.")
                        continue

                log.info("Generating and sending tracer flare")
                flare.revert_configs()
                flare.send(flare_action)
                state.current_request_start = None
            elif flare_action.is_unset():
                log.info("Reverting tracer flare configurations and cleaning up any generated files")
                flare.revert_configs()
                flare.clean_up_files()
                state.current_request_start = None
            else:
                log.warning("Received unexpected product type for tracer flare: %s", product_type)


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

        _process_payloads(self._flare, self._state, payloads)


class TracerFlareSubscriber(RemoteConfigSubscriber):
    """Compatibility subscriber retained for existing unit tests."""

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

        state = TracerFlareState()
        state.current_request_start = self.current_request_start
        _process_payloads(self.flare, state, data)
        self.current_request_start = state.current_request_start
