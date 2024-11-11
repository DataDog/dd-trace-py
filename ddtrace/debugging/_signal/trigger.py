from dataclasses import dataclass
import typing as t

from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.model import SessionMixin
from ddtrace.debugging._session import Session
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class Trigger(LogSignal):
    """Trigger a session creation."""

    __default_timing__ = ProbeEvalTiming.ENTRY

    def _link_session(self) -> None:
        probe = t.cast(SessionMixin, self.probe)
        session = Session(probe.session_id, probe.level)

        # Link the session to the running trace
        session.link_to_trace(self.trace_context)

        # Ensure that the new session information is included in the debug
        # propagation tag for distributed debugging
        session.propagate(self.trace_context)

    def enter(self, scope: t.Mapping[str, t.Any]) -> None:
        self._link_session()

    def exit(self, retval: t.Any, exc_info: ExcInfoType, duration: float, scope: t.Mapping[str, t.Any]) -> None:
        session = self.session
        if session is not None:
            session.unlink_from_trace(self.trace_context)

    def line(self, scope: t.Mapping[str, t.Any]):
        self._link_session()

    @property
    def message(self):
        return f"Condition evaluation errors for probe {self.probe.probe_id}" if self.errors else None

    def has_message(self) -> bool:
        return bool(self.errors)
