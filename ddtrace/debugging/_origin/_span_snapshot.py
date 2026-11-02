from dataclasses import dataclass
from time import monotonic_ns
from types import FrameType
import typing as t

from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._signal.model import TraceContextProtocol
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.native import uuid4
from ddtrace.internal.threads import current_thread


@dataclass
class EntrySpanProbe(LogFunctionProbe):
    __span_class__ = "entry"

    def is_sampled(self) -> bool:
        # Code origin snapshots are not coordinated with Live Debugger probes.
        return False

    @classmethod
    def build(cls, name: str, module: str, function: str) -> "EntrySpanProbe":
        message = f"{cls.__span_class__} span info for {name}, in {module}, in function {function}"

        return cls(
            probe_id=uuid4(),
            version=0,
            tags={},
            module=module,
            func_qname=function,
            evaluate_at=ProbeEvalTiming.ENTRY,
            template=message,
            segments=[LiteralTemplateSegment(message)],
            take_snapshot=True,
            capture_expressions=[],
            limits=DEFAULT_CAPTURE_LIMITS,
            condition=None,
            condition_error_rate=0.0,
            rate=float("inf"),
        )


def capture_snapshot(
    probe: EntrySpanProbe,
    frame: FrameType,
    root: t.Any,
    span: t.Any,
    start_time: int,
    retval: t.Any,
    exc_info: ExcInfoType,
    uploader: type[SignalUploader],
) -> None:
    snapshot = Snapshot(
        probe=probe,
        frame=frame,
        thread=current_thread(),
        trace_context=t.cast(TraceContextProtocol, root),
    )
    snapshot.do_enter()
    root._set_attribute("_dd.code_origin.frames.0.snapshot_id", snapshot.uuid)
    span._set_attribute("_dd.code_origin.frames.0.snapshot_id", snapshot.uuid)
    snapshot.do_exit(retval, exc_info, monotonic_ns() - start_time)
    if (collector := uploader.get_collector()) is not None:
        collector.push(snapshot)
