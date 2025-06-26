import os

import pytest

from tests.contrib.google_genai.utils import FULL_GENERATE_CONTENT_CONFIG
from tests.contrib.google_genai.utils import get_expected_metadata
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsGoogleGenAI:
    @pytest.mark.parametrize("is_vertex", [False, True])
    def test_generate_content(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content, is_vertex):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    def test_generate_content_error(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content, is_vertex):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        with pytest.raises(TypeError):
            client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    def test_generate_content_stream(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content_stream, is_vertex):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        response = client.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        for _ in response:
            pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    def test_generate_content_stream_error(self, genai, mock_llmobs_writer, mock_tracer, mock_generate_content_stream, is_vertex):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        with pytest.raises(TypeError):
            client.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    async def test_generate_content_async(self, genai, mock_llmobs_writer, mock_tracer, mock_async_generate_content, is_vertex):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        await client.aio.models.generate_content(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    async def test_generate_content_async_error(
        self, genai, mock_llmobs_writer, mock_tracer, mock_async_generate_content, is_vertex
    ):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        with pytest.raises(TypeError):
            await client.aio.models.generate_content(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    async def test_generate_content_stream_async(
        self, genai, mock_llmobs_writer, mock_tracer, mock_async_generate_content_stream, is_vertex
    ):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        response = await client.aio.models.generate_content_stream(
            model="gemini-2.0-flash-001",
            contents="Why is the sky blue? Explain in 2-3 sentences.",
            config=FULL_GENERATE_CONTENT_CONFIG,
        )
        async for _ in response:
            pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_span_event(span))

    @pytest.mark.parametrize("is_vertex", [False, True])
    async def test_generate_content_stream_async_error(
        self, genai, mock_llmobs_writer, mock_tracer, mock_async_generate_content_stream, is_vertex
    ):
        if is_vertex:
            client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            client = genai.Client()
        
        with pytest.raises(TypeError):
            await client.aio.models.generate_content_stream(
                model="gemini-2.0-flash-001",
                contents="Why is the sky blue? Explain in 2-3 sentences.",
                config=FULL_GENERATE_CONTENT_CONFIG,
                not_an_argument="why am i here?",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(expected_llmobs_error_span_event(span))


def expected_llmobs_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"},
        ],
        output_messages=[{"content": "The sky is blue due to rayleigh scattering", "role": "model"}],
        metadata=get_expected_metadata(),
        token_metrics={"input_tokens": 8, "output_tokens": 9, "total_tokens": 17},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )


def expected_llmobs_error_span_event(span):
    return _expected_llmobs_llm_span_event(
        span,
        model_name="gemini-2.0-flash-001",
        model_provider="google",
        input_messages=[
            {"content": "You are a helpful assistant.", "role": "system"},
            {"content": "Why is the sky blue? Explain in 2-3 sentences.", "role": "user"},
        ],
        output_messages=[{"content": ""}],
        error="builtins.TypeError",
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
        metadata=get_expected_metadata(),
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.google_genai"},
    )
