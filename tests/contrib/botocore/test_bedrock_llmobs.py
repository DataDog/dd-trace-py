import json

import mock
import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.pin import Pin
from tests.contrib.botocore.utils import _MODELS
from tests.contrib.botocore.utils import _REQUEST_BODIES
from tests.contrib.botocore.utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer
from tests.utils import DummyWriter


vcr = pytest.importorskip("vcr")


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


class TestLLMObsBedrock:
    @staticmethod
    def expected_llmobs_span_event(span, n_output, message=False):
        prompt_tokens = int(span.get_tag("bedrock.usage.prompt_tokens"))
        completion_tokens = int(span.get_tag("bedrock.usage.completion_tokens"))
        expected_parameters = {"temperature": float(span.get_tag("bedrock.request.temperature"))}
        if span.get_tag("bedrock.request.max_tokens"):
            expected_parameters["max_tokens"] = int(span.get_tag("bedrock.request.max_tokens"))
        expected_input = [{"content": mock.ANY}]
        if message:
            expected_input = [{"content": mock.ANY, "role": "user"}]
        return _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("bedrock.request.model"),
            model_provider=span.get_tag("bedrock.request.model_provider"),
            input_messages=expected_input,
            output_messages=[{"content": mock.ANY} for _ in range(n_output)],
            metadata=expected_parameters,
            token_metrics={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @classmethod
    def _test_llmobs_invoke(cls, provider, bedrock_client, mock_llmobs_span_writer, cassette_name=None, n_output=1):
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin.override(bedrock_client, tracer=mock_tracer)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, _tracer=mock_tracer)

        if cassette_name is None:
            cassette_name = "%s_invoke.yaml" % provider
        body = _REQUEST_BODIES[provider]
        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": False,
                "num_generations": n_output,
            }
        with get_request_vcr().use_cassette(cassette_name):
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_called_with(
            cls.expected_llmobs_span_event(span, n_output, message="message" in provider)
        )
        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke_stream(
        cls, provider, bedrock_client, mock_llmobs_span_writer, cassette_name=None, n_output=1
    ):
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin.override(bedrock_client, tracer=mock_tracer)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, _tracer=mock_tracer)

        if cassette_name is None:
            cassette_name = "%s_invoke_stream.yaml" % provider
        body = _REQUEST_BODIES[provider]
        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": True,
                "num_generations": n_output,
            }
        with get_request_vcr().use_cassette(cassette_name):
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
            for _ in response.get("body"):
                pass
        span = mock_tracer.pop_traces()[0][0]

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_called_with(
            cls.expected_llmobs_span_event(span, n_output, message="message" in provider)
        )
        LLMObs.disable()

    def test_llmobs_ai21_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("ai21", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_amazon_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("amazon", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("anthropic", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_message(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("anthropic_message", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_cohere_single_output_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke(
            "cohere", bedrock_client, mock_llmobs_span_writer, cassette_name="cohere_invoke_single_output.yaml"
        )

    def test_llmobs_cohere_multi_output_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("meta", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_amazon_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("amazon", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("anthropic", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_message_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("anthropic_message", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_cohere_single_output_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_stream_single_output.yaml",
        )

    def test_llmobs_cohere_multi_output_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_stream_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream(self, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("meta", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_error(self, bedrock_client, mock_llmobs_span_writer, request_vcr):
        import botocore

        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin.override(bedrock_client, tracer=mock_tracer)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False, _tracer=mock_tracer)
        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("meta_invoke_error.yaml"):
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        expected_llmobs_writer_calls = [
            mock.call.start(),
            mock.call.enqueue(
                _expected_llmobs_llm_span_event(
                    span,
                    model_name=span.get_tag("bedrock.request.model"),
                    model_provider=span.get_tag("bedrock.request.model_provider"),
                    input_messages=[{"content": mock.ANY}],
                    metadata={
                        "temperature": float(span.get_tag("bedrock.request.temperature")),
                        "max_tokens": int(span.get_tag("bedrock.request.max_tokens")),
                    },
                    output_messages=[{"content": ""}],
                    error=span.get_tag("error.type"),
                    error_message=span.get_tag("error.message"),
                    error_stack=span.get_tag("error.stack"),
                    tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
                )
            ),
        ]

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.assert_has_calls(expected_llmobs_writer_calls)
        LLMObs.disable()
