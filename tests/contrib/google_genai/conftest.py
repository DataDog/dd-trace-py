import os
from typing import Any
from typing import Iterator
from unittest.mock import patch as mock_patch

import pytest

from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter


@pytest.fixture
def mock_tracer(genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
def genai():
    patch()
    from google import genai

    # tests require that these environment variables are set (especially for remote CI testing)
    os.environ["GOOGLE_CLOUD_LOCATION"] = "<not-a-real-location>"
    os.environ["GOOGLE_CLOUD_PROJECT"] = "<not-a-real-project>"
    os.environ["GOOGLE_API_KEY"] = "<not-a-real-key>"

    yield genai
    unpatch()


@pytest.fixture
def mock_generate_content():
    from google import genai
    from google.genai import types

    candidate = types.Candidate(
        content=types.Content(
            role="user", parts=[types.Part.from_text(text="The sky is blue due to rayleigh scattering")]
        )
    )
    _response = types.GenerateContentResponse(candidates=[candidate])

    def _fake_stream(self, *, model: str, contents, config=None) -> Iterator[Any]:
        yield _response

    def _fake_generate_content(self, *, model: str, contents, config=None):
        return _response

    async def _fake_async_stream(self, *, model: str, contents, config=None):
        async def _async_iterator():
            yield _response

        return _async_iterator()

    async def _fake_async_generate_content(self, *, model: str, contents, config=None):
        return _response

    with mock_patch.object(genai.models.Models, "_generate_content_stream", _fake_stream), mock_patch.object(
        genai.models.Models, "_generate_content", _fake_generate_content
    ), mock_patch.object(genai.models.AsyncModels, "_generate_content_stream", _fake_async_stream), mock_patch.object(
        genai.models.AsyncModels, "_generate_content", _fake_async_generate_content
    ):
        yield
