from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import requests


# Service name is required for most backends
resource = Resource(attributes={SERVICE_NAME: "otel-w3c-service"})

provider = TracerProvider(resource=resource)
exp = OTLPSpanExporter(endpoint="http://0.0.0.0:4318/v1/traces")
processor = BatchSpanProcessor(exp)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("opentelemetry-w3c-manual-span-parent") as span:
    curr = span.get_span_context()
    span_context = trace.SpanContext(
        curr.trace_id,
        curr.span_id,
        is_remote=False,
        trace_flags=trace.TraceFlags(0x01),
        trace_state=trace.TraceState((("munir_key", "munir_:_colonvalue"), ("test_ts_key", "test_!ts_val"))),
    )

    ctx = trace.set_span_in_context(trace.NonRecordingSpan(span_context))
    with tracer.start_as_current_span("opentelemetry-w3c-manual-span-child", context=ctx) as child:
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        resp = requests.get("http://localhost:8000/otel", headers=carrier)
        assert resp.status_code == 200
        print("Hello world!")
