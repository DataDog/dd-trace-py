import os

import pytest

from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


@pytest.fixture
def mock_tracer(genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        pin.tracer.configure()
        yield mock_tracer
    except Exception as e:
        yield


@pytest.fixture
def genai():
    patch()
    from google import genai

    # When testing locally to generate new cassette files, comment the line below to use the real Google API key
    os.environ["GOOGLE_API_KEY"] = "<not-a-real-key>"

    yield genai
    unpatch()
