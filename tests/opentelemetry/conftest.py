import opentelemetry
import pytest

from ddtrace.opentelemetry import TracerProvider


TRACER_PROVIDER = TracerProvider()
# set_tracer_provider can only be called once
opentelemetry.trace.set_tracer_provider(TRACER_PROVIDER)


@pytest.fixture
def oteltracer():
    assert opentelemetry.trace.get_tracer_provider() is TRACER_PROVIDER
    yield opentelemetry.trace.get_tracer(__name__)
