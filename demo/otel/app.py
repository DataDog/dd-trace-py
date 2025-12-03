"""
Example httpx application with OpenTelemetry automatic instrumentation
Sends telemetry data to OTel Collector, which forwards to Datadog

Run with: opentelemetry-instrument python app.py
"""
import emoji
from opentelemetry import trace
from ddtrace import patch

patch(emoji=True)

def main():
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("parent_span_of_emojize"):
        print(emoji.emojize('Python is :thumbs_up:'))


if __name__ == "__main__":
    main()

