import os

import opentelemetry
import pytest

from ddtrace.opentelemetry import TracerProvider


TRACER_PROVIDER = TracerProvider()
# set_tracer_provider can only be called once
opentelemetry.trace.set_tracer_provider(TRACER_PROVIDER)


@pytest.fixture
def set_otel_python_context():
    # OTEL_PYTHON_CONTEXT overrides the default contextvar used to parent otel spans. Setting this envar to
    # ``ddcontextvars_context`` allows us correlate otel and ddtrace spans by load the following
    # ddtrace entry point: `ddcontextvars_context = ddtrace.internal.opentelemetry.context:DDRuntimeContext`
    os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"


@pytest.fixture
def oteltracer(set_otel_python_context):
    assert opentelemetry.trace.get_tracer_provider() is TRACER_PROVIDER
    yield opentelemetry.trace.get_tracer(__name__)
