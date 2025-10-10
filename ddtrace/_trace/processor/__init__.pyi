from threading import RLock
from typing import Dict, List, Optional

from ddtrace._trace.span import Span
from ddtrace.internal.writer.writer import TraceWriter

class TraceProcessor:
    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]: ...

class SpanProcessor:
    def on_span_start(self, span: Span) -> None: ...
    def on_span_finish(self, span: Span) -> None: ...
    def shutdown(self, timeout: Optional[float]) -> None: ...
    def register(self) -> None: ...
    def unregister(self) -> None: ...

class TraceSamplingProcessor(TraceProcessor): ...
class TopLevelSpanProcessor(SpanProcessor): ...
class ServiceNameProcessor(TraceProcessor): ...
class TraceTagsProcessor(TraceProcessor): ...

class SpanAggregator(SpanProcessor):
    partial_flush_enabled: bool
    partial_flush_min_spans: int
    sampling_processor: TraceSamplingProcessor
    tags_processor: TraceTagsProcessor
    service_name_processor: ServiceNameProcessor
    dd_processors: List[TraceProcessor]
    user_processors: List[TraceProcessor]
    writer: TraceWriter
    _traces: Dict[int, List[Span]]
    _spans_created: Dict[str, int]
    _spans_finished: Dict[str, int]
    _lock: RLock
    _total_spans_finished: int

    def __init__(
        self,
        partial_flush_enabled: bool,
        partial_flush_min_spans: int,
        dd_processors: Optional[List[TraceProcessor]] = None,
        user_processors: Optional[List[TraceProcessor]] = None,
    ) -> None: ...
