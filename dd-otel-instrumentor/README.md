# `ddtrace-otel-instrumentor`

This package provides Datadog's instrumentation integrations with OpenTelemetry SDK .

## Installation

```bash
# Basic installation
pip install dd-otel-instrumentor

# With httpx integration
pip install dd-otel-instrumentor[httpx]

# With all integrations
pip install dd-otel-instrumentor[all]
```


## Usage Example

```python
# Set up OpenTelemetry SDK
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    SimpleSpanProcessor(ConsoleSpanExporter())
)

# Patch httpx with DD instrumentation (automatically enables OTel mode)
from dd_otel_instrumentor import patch
patch("httpx")

# Make requests - spans are created with OTel SDK
import httpx
response = httpx.get("https://example.com")
```

You can also patch multiple integrations at once:

```python
from dd_otel_instrumentor import patch
patch("httpx", "requests", "flask")
```
