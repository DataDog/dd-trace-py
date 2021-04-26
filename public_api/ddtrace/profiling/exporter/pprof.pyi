import Any
import pprof_pb2 as pprof_pb2
from ddtrace.profiling import exporter

class PprofExporter(exporter.Exporter):
    @staticmethod
    def min_none(a: Any, b: Any) -> Any: ...
    @staticmethod
    def max_none(a: Any, b: Any) -> Any: ...
    def export(self, events: Any, start_time_ns: Any, end_time_ns: Any) -> pprof_pb2.Profile: ...
