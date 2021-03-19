from typing import Any

from ddtrace.profiling import exporter
from ddtrace.profiling.exporter import pprof_pb2

class _Sequence:
    start_at: Any = ...
    next_id: Any = ...
    def __attrs_post_init__(self) -> None: ...
    def generate(self) -> int: ...

class _StringTable:
    def to_id(self, string: str) -> int: ...
    def __iter__(self) -> Any: ...
    def __len__(self) -> int: ...

class _PprofConverter:
    def convert_stack_event(
        self,
        thread_id: Any,
        thread_native_id: Any,
        thread_name: Any,
        trace_id: Any,
        span_id: Any,
        frames: Any,
        nframes: Any,
        samples: Any,
    ) -> None: ...
    def convert_memalloc_event(
        self, thread_id: Any, thread_native_id: Any, thread_name: Any, frames: Any, nframes: Any, events: Any
    ) -> None: ...
    def convert_memalloc_heap_event(self, event: Any) -> None: ...
    def convert_lock_acquire_event(
        self,
        lock_name: Any,
        thread_id: Any,
        thread_name: Any,
        trace_id: Any,
        span_id: Any,
        frames: Any,
        nframes: Any,
        events: Any,
        sampling_ratio: Any,
    ) -> None: ...
    def convert_lock_release_event(
        self,
        lock_name: Any,
        thread_id: Any,
        thread_name: Any,
        trace_id: Any,
        span_id: Any,
        frames: Any,
        nframes: Any,
        events: Any,
        sampling_ratio: Any,
    ) -> None: ...
    def convert_stack_exception_event(
        self,
        thread_id: Any,
        thread_native_id: Any,
        thread_name: Any,
        trace_id: Any,
        span_id: Any,
        frames: Any,
        nframes: Any,
        exc_type_name: Any,
        events: Any,
    ) -> None: ...
    def convert_memory_event(self, stats: Any, sampling_ratio: Any) -> None: ...

class PprofExporter(exporter.Exporter):
    @staticmethod
    def min_none(a: Any, b: Any): ...
    @staticmethod
    def max_none(a: Any, b: Any): ...
    def export(self, events: Any, start_time_ns: Any, end_time_ns: Any) -> pprof_pb2.Profile: ...  # type: ignore[valid-type]
