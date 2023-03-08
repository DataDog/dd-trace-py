import typing as t

import attr

import ddtrace
from ddtrace import Span
from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._probe.model import SpanFunctionProbe
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

SPAN_NAME = "dd.dynamic.span"
PROBE_ID_TAG_NAME = "debugger.probeid"


@attr.s
class DynamicSpan(CapturedEvent):
    """Dynamically created span"""

    span = attr.ib(type=t.Optional[Span], default=None)

    def enter(self):
        # type: () -> None
        probe = self.probe
        if not isinstance(probe, SpanFunctionProbe):
            log.debug("Dynamic span entered with non-span probe: %s", self.probe)
            return

        _args = dict(self.args) if self.args else {}
        if not self._eval_condition(_args):
            return

        tracer = ddtrace.tracer

        span = tracer.start_span(
            SPAN_NAME,
            child_of=tracer.context_provider.active(),
            service=None,  # TODO
            resource=probe.func_qname,
            span_type=None,  # TODO
            activate=True,
        )
        span.set_tags(probe.tags)
        span.set_tag(PROBE_ID_TAG_NAME, probe.probe_id)

        self.span = span.__enter__()

        self.state = CaptureState.DONE

    def exit(self, retval, exc_info, duration):
        # type: (t.Any, ExcInfoType, float) -> None
        if not isinstance(self.probe, SpanFunctionProbe):
            log.debug("Dynamic span exited with non-span probe: %s", self.probe)
            return

        if not self.span:
            log.debug("Dynamic span exited without span: %s", self.probe)
            return

        self.span.__exit__(*exc_info)

    def line(self, _locals=None, exc_info=(None, None, None)):
        raise NotImplementedError("Dynamic line spans are not supported in Python")
