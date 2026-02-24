"""
OTLP trace export: mapper, JSON serializer, and HTTP exporter.

HTTP/JSON only for the first version. Maps Datadog spans to OTLP
ExportTraceServiceRequest and POSTs to the configured OTLP endpoint with retries.
"""

from .exporter import OTLPHttpTraceExporter
from .mapper import dd_trace_to_otlp_request
from .serializer import otlp_request_to_json_bytes


__all__ = [
    "OTLPHttpTraceExporter",
    "dd_trace_to_otlp_request",
    "otlp_request_to_json_bytes",
]
