from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import typing as t

from ddtestpy.ddtrace_interface import CoverageData
from ddtestpy.ddtrace_interface import PushSpanProtocol
from ddtestpy.ddtrace_interface import TraceContext
from ddtestpy.ddtrace_interface import TracerInterface
from ddtestpy.ddtrace_interface import register_tracer_interface

from ddtrace.trace import TraceFilter


if t.TYPE_CHECKING:
    from ddtrace.trace import Span


class TraceForwarder(TraceFilter):
    def __init__(self, push_span: PushSpanProtocol) -> None:
        self.push_span = push_span

    def process_trace(self, trace: t.List[Span]) -> t.Optional[t.List[Span]]:
        for span in trace:
            self.push_span(
                trace_id=span.trace_id,
                parent_id=span.parent_id,
                span_id=span.span_id,
                service=span.service,
                resource=span.resource,
                name=span.name,
                error=span.error,
                start_ns=span.start_ns,
                duration_ns=span.duration_ns,
                meta=span.get_tags(),
                metrics=span.get_metrics(),
                span_type=span.span_type,
            )
        return None


class DDTraceInterface(TracerInterface):
    def __init__(self) -> None:
        self.workspace_path: t.Optional[Path] = None
        self._should_enable_test_optimization: bool = False
        self._should_enable_trace_collection: bool = False

    def should_enable_test_optimization(self) -> bool:
        return self._should_enable_test_optimization

    def should_enable_trace_collection(self) -> bool:
        return self._should_enable_trace_collection

    def enable_trace_collection(self, push_span: PushSpanProtocol) -> None:
        import ddtrace

        ddtrace.tracer.configure(trace_processors=[TraceForwarder(push_span)])

    def disable_trace_collection(self) -> None:
        import ddtrace

        ddtrace.tracer.configure(trace_processors=[])

    @contextmanager
    def trace_context(self, resource: str) -> t.Generator[TraceContext, None, None]:
        import ddtrace

        # TODO: check if this breaks async tests.
        # This seems to be necessary because buggy ddtrace integrations can leave spans
        # unfinished, and spans for subsequent tests will have the wrong parent.
        ddtrace.tracer.context_provider.activate(None)

        with ddtrace.tracer.trace(resource) as root_span:
            yield TraceContext(root_span.trace_id, root_span.span_id)

    def enable_coverage_collection(self, workspace_path: Path) -> None:
        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        install(include_paths=[workspace_path], collect_import_time_coverage=True)
        ModuleCodeCollector.start_coverage()

    def disable_coverage_collection(self) -> None:
        ModuleCodeCollector.stop_coverage()

    @contextmanager
    def coverage_context(self) -> t.Generator[CoverageData, None, None]:
        with ModuleCodeCollector.CollectInContext() as coverage_collector:
            coverage_data = CoverageData()
            yield coverage_data
            covered_lines_by_file = coverage_collector.get_covered_lines()

        coverage_data.bitmaps = {}
        for absolute_path, covered_lines in covered_lines_by_file.items():
            try:
                relative_path = Path(absolute_path).relative_to(self.workspace_path)
            except ValueError:
                continue  # covered file does not belong to current repo

            path_str = f"/{relative_path}"
            coverage_data.bitmaps[path_str] = covered_lines.to_bytes()


ddtrace_interface = DDTraceInterface()

register_tracer_interface(ddtrace_interface)
