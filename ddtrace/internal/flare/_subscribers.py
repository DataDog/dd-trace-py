from datetime import datetime
import typing as t
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


def _content_as_list(content: t.Any) -> list:
    """Normalize payload content to a list (handles both list and dict from tests)."""
    return content if isinstance(content, list) else [content]


def _process_payloads(flare: Flare, state: TracerFlareState, payloads: t.Sequence[Payload]) -> None:
    for payload in payloads:
        if payload.metadata is None:
            log.debug("Flare: flare payload missing metadata, path=%r", payload.path)
            continue
        elif isinstance(payload.metadata.id, str) and payload.metadata.id.lower() == "configuration_order":
            continue

        product_type = payload.metadata.product_name
        items = _content_as_list(payload.content)
        for item in items:
            if not isinstance(item, dict):
                continue

            flare_action = flare.handle_remote_config_data(item, product_type)
            if flare_action.is_set():
                if state.current_request_start is not None:
                    log.debug(
                        "Flare: collection already in progress (started %s), ignoring request path=%r",
                        state.current_request_start,
                        payload.path,
                    )
                    continue
                if flare_action.level is None or not isinstance(flare_action.level, str):
                    log.warning(
                        "Flare: received set flare action without log level. Level: %s, Case ID: %s. Skipping...",
                        flare_action.level,
                        flare_action.case_id,
                    )
                    continue
                if flare.prepare(flare_action.level):
                    state.current_request_start = datetime.now()

            elif flare_action.is_send():
                if state.current_request_start is None:
                    if flare.prepare("DEBUG"):
                        state.current_request_start = datetime.now()
                    else:
                        continue
                flare.revert_configs()
                flare.send(flare_action)
                state.current_request_start = None
            elif flare_action.is_unset():
                flare.revert_configs()
                flare.clean_up_files()
                state.current_request_start = None
            else:
                # none_action is expected when the payload is not relevant to us
                # (e.g. configuration_order for AGENT_CONFIG, wrong task_type for AGENT_TASK)
                log.debug(
                    "Flare: skipping non-actionable payload for product %s. Case ID: %r.",
                    product_type,
                    flare_action.case_id,
                )


class TracerFlareCallback(RCCallback):
    """Remote config callback for tracer flare requests.

    periodic() runs stale flare checks each poll; __call__() processes AGENT_CONFIG / AGENT_TASK payloads.
    """

    def __init__(
        self,
        flare: Flare,
        state: TracerFlareState,
        stale_duration_mins: int = DEFAULT_STALE_FLARE_DURATION_MINS,
    ) -> None:
        """Initialize the tracer flare callback.

        Args:
            flare: Flare instance for generating flares
            state: Shared state for tracking flare requests
            stale_duration_mins: Minutes before a flare request is considered stale
        """
        self._flare = flare
        self._state = state
        self._stale_duration_secs = stale_duration_mins * 60

    def _has_stale_flare(self) -> bool:
        """Check if the current flare request is stale."""
        if not self._state.current_request_start:
            return False
        age_secs = (datetime.now() - self._state.current_request_start).total_seconds()
        return age_secs >= self._stale_duration_secs

    def periodic(self) -> None:
        """Periodic method called at every polling operation.

        Checks for stale flare requests and cleans them up if necessary.
        """
        if not self._has_stale_flare():
            return
        log.debug(
            "Flare: flare collection timed out (started %s), stopping collection and cleaning up",
            self._state.current_request_start,
        )
        self._state.current_request_start = None
        self._flare.revert_configs()
        self._flare.clean_up_files()

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process AGENT_CONFIG (start) and AGENT_TASK (generate/send) payloads."""
        if not payloads:
            return

        _process_payloads(self._flare, self._state, payloads)


class TracerFlareSubscriber(RemoteConfigSubscriber):
    """Subscriber for tracer flare requests."""

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
