from collections import ChainMap
from dataclasses import dataclass
import typing as t

from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.model import SessionMixin
from ddtrace.debugging._probe.model import TriggerFunctionProbe
from ddtrace.debugging._probe.model import TriggerLineProbe
from ddtrace.debugging._session import Session
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class Trigger(LogSignal):
    """Trigger a session creation."""

    def _link_session(self) -> None:
        probe = t.cast(SessionMixin, self.probe)
        Session(probe.session_id, probe.level).link_to_trace(self.trace_context)

    def enter(self) -> None:
        probe = self.probe
        if not isinstance(probe, TriggerFunctionProbe):
            log.debug("Trigger probe entered with non-trigger probe: %s", self.probe)
            return

        if probe.evaluate_at not in (ProbeEvaluateTimingForMethod.ENTER, ProbeEvaluateTimingForMethod.DEFAULT):
            return

        if not self._eval_condition(ChainMap(self.args, self.frame.f_globals)):
            return

        self._link_session()

        self.state = SignalState.DONE

    def exit(self, retval: t.Any, exc_info: ExcInfoType, duration: float) -> None:
        probe = self.probe

        if not isinstance(probe, TriggerFunctionProbe):
            log.debug("Trigger probe exited with non-trigger probe: %s", self.probe)
            return

        if probe.evaluate_at is not ProbeEvaluateTimingForMethod.EXIT:
            return

        if not self._eval_condition(self.get_full_scope(retval, exc_info, duration)):
            return

        self._link_session()

        self.state = SignalState.DONE

    def line(self):
        probe = self.probe
        if not isinstance(probe, TriggerLineProbe):
            log.debug("Span decoration on line with non-span decoration probe: %s", self.probe)
            return

        frame = self.frame

        if not self._eval_condition(ChainMap(frame.f_locals, frame.f_globals)):
            return

        self._link_session()

        self.state = SignalState.DONE

    @property
    def message(self):
        return f"Condition evaluation errors for probe {self.probe.probe_id}" if self.errors else None

    def has_message(self) -> bool:
        return bool(self.errors)
