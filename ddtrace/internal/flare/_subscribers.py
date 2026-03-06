from datetime import datetime
import typing as t
from typing import Optional

from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.handler import cleanup_tracer_flare
from ddtrace.internal.flare.handler import generate_tracer_flare
from ddtrace.internal.flare.handler import prepare_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback


log = get_logger(__name__)

DEFAULT_STALE_FLARE_DURATION_MINS = 20


class TracerFlareState:
    """Shared state between the callback and stale check service."""

    def __init__(self) -> None:
        self.current_request_start: Optional[datetime] = None


def _content_as_list(content: t.Any) -> list:
    """Normalize payload content to a list (handles both list and dict from tests)."""
    return content if isinstance(content, list) else [content]


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
        self._flare = flare
        self._state = state
        self._stale_duration_secs = stale_duration_mins * 60

    def _has_stale_flare(self) -> bool:
        if not self._state.current_request_start:
            return False
        age_secs = (datetime.now() - self._state.current_request_start).total_seconds()
        return age_secs >= self._stale_duration_secs

    def periodic(self) -> None:
        """Run on each poll; clean up if the current flare request has gone stale."""
        if not self._has_stale_flare():
            return
        log.debug(
            "Remote config: flare collection timed out (started %s), stopping collection and cleaning up",
            self._state.current_request_start,
        )
        self._state.current_request_start = None
        cleanup_tracer_flare(self._flare)

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process AGENT_CONFIG (start) and AGENT_TASK (generate/send) payloads."""
        if not payloads:
            return
        for payload in payloads:
            if payload.metadata is None:
                log.debug("Remote config: flare payload missing metadata, path=%r", payload.path)
                continue
            product_type = payload.metadata.product_name
            data = _content_as_list(payload.content)

            if product_type == "AGENT_CONFIG":
                if self._state.current_request_start is not None:
                    log.debug(
                        "Remote config: flare collection already in progress (started %s), "
                        "ignoring preparation request path=%r",
                        self._state.current_request_start,
                        payload.path,
                    )
                    continue
                if prepare_tracer_flare(self._flare, data):
                    self._state.current_request_start = datetime.now()

            elif product_type == "AGENT_TASK":
                if self._state.current_request_start is None:
                    if not self._flare.prepare("DEBUG"):
                        # flare.prepare() already logs the reason
                        continue
                    self._state.current_request_start = datetime.now()
                if generate_tracer_flare(self._flare, data):
                    self._state.current_request_start = None

            else:
                log.warning(
                    "Remote config: unknown flare request type %s, path=%r",
                    product_type,
                    payload.path,
                )
