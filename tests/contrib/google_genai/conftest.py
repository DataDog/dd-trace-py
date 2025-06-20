import os

import mock
import pytest
from typing import Any, Iterator, AsyncIterator

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

    # When testing locally to generate new cassette files,
    # comment the lines below to use the real Google API key
    os.environ["GOOGLE_API_KEY"] = "<not-a-real-key>"

    yield genai
    unpatch()

@pytest.fixture
def mock_vertex_generate_content(monkeypatch):
    """
    Vertex enabled genAI clients are difficult to test with VCRpy due to their use of google auth.
    Instead we patch the generate_content and generate_content_stream methods (sync and async) to return a mock response.
    """
    from google import genai
    from google.genai import types

    candidate = types.Candidate(
        content=types.Content(role='user', parts=[types.Part.from_text(text='The sky is blue due to rayleigh scattering')])
    )
    _response = types.GenerateContentResponse(candidates=[candidate])

    def _fake_stream(self, *, model: str, contents, config=None) -> Iterator[Any]:
        yield _response

    def _fake_generate_content(self, *, model: str, contents, config=None):
        return _response

    async def _fake_async_stream(
        self, *, model: str, contents, config=None
    ):
        async def _async_iterator():
            yield _response
        return _async_iterator()

    async def _fake_async_generate_content(self, *, model: str, contents, config=None):
        return _response

    monkeypatch.setattr(genai.models.Models, "_generate_content_stream", _fake_stream)
    monkeypatch.setattr(genai.models.Models, "_generate_content", _fake_generate_content)
    monkeypatch.setattr(genai.models.AsyncModels, "_generate_content_stream", _fake_async_stream)
    monkeypatch.setattr(genai.models.AsyncModels, "_generate_content", _fake_async_generate_content)

    yield