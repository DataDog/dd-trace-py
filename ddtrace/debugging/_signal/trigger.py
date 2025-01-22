from dataclasses import dataclass
import typing as t

from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.model import SessionMixin
from ddtrace.debugging._probe.model import TriggerFunctionProbe
from ddtrace.debugging._probe.model import TriggerLineProbe
from ddtrace.debugging._session import Session
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import probe_to_signal
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class Trigger(LogSignal):
    """Trigger a session creation."""

    __default_timing__ = ProbeEvalTiming.ENTRY

    def _link_session(self) -> None:
        probe = t.cast(SessionMixin, self.probe)

        if Session.is_active(probe.session_id):
            # This trigger probe has already been triggered along the current
            # trace. We don't want to create a new session for the same
            # session ID.
            return

        session = Session(probe.session_id, probe.level)

        # Link the session to the running trace
        session.link_to_trace(self.trace_context)

        # Ensure that the new session information is included in the debug
        # propagation tag for distributed debugging
        session.propagate(self.trace_context)

        # DEV: Unfortunately we don't have an API for this :(
        self.trace_context._meta[f"_dd.ld.probe_id.{self.probe.probe_id}"] = "true"  # type: ignore[union-attr]

    def enter(self, scope: t.Mapping[str, t.Any]) -> None:
        self._link_session()

    def exit(self, retval: t.Any, exc_info: ExcInfoType, duration: float, scope: t.Mapping[str, t.Any]) -> None:
        # DEV: We do not unlink on exit explicitly here. We let the weak
        # reference to the context object do the job.
        pass

    def line(self, scope: t.Mapping[str, t.Any]):
        self._link_session()

    @property
    def message(self):
        return f"Condition evaluation errors for probe {self.probe.probe_id}" if self.errors else None

    def has_message(self) -> bool:
        return bool(self.errors)


@probe_to_signal.register
def _(probe: TriggerFunctionProbe, frame, thread, trace_context, meter):
    return Trigger(probe=probe, frame=frame, thread=thread, trace_context=trace_context)


@probe_to_signal.register
def _(probe: TriggerLineProbe, frame, thread, trace_context, meter):
    return Trigger(probe=probe, frame=frame, thread=thread, trace_context=trace_context)
