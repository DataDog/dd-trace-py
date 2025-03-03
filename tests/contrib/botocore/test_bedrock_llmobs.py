import json
import os

import boto3
import mock
import pytest

from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import unpatch
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import AGENTLESS_BASE_URL
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Pin
from tests.contrib.botocore.bedrock_utils import _MODELS
from tests.contrib.botocore.bedrock_utils import _REQUEST_BODIES
from tests.contrib.botocore.bedrock_utils import create_bedrock_converse_request
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


BOTO_VERSION = parse_version(boto3.__version__)


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
def ddtrace_global_config():
    config = {}
    return config


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials. To regenerate test cassettes, comment this out and use real credentials."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def boto3(aws_credentials, mock_llmobs_span_writer, ddtrace_global_config):
    global_config = {"_dd_api_key": "<not-a-real-api_key>"}
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        patch()
        import boto3

        yield boto3
        unpatch()


@pytest.fixture
def bedrock_client(boto3, request_vcr):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    bedrock_client = session.client("bedrock-runtime")
    yield bedrock_client


@pytest.fixture
def mock_llmobs_span_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def enqueue(self, event):
        self.events.append(event)


@pytest.fixture
def llmobs_span_writer():
    agentless_url = "{}.{}".format(AGENTLESS_BASE_URL, "datad0g.com")
    yield TestLLMObsSpanWriter(is_agentless=True, agentless_url=agentless_url, interval=1.0, timeout=1.0)


@pytest.fixture
def mock_tracer(bedrock_client):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(bedrock_client)
    pin._override(bedrock_client, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def bedrock_llmobs(tracer, mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.llmobs"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(bedrock_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsBedrock:
    @staticmethod
    def expected_llmobs_span_event(span, n_output, message=False):
        prompt_tokens = span.get_metric("bedrock.response.usage.prompt_tokens")
        completion_tokens = span.get_metric("bedrock.response.usage.completion_tokens")
        token_metrics = {}
        if prompt_tokens is not None:
            token_metrics["input_tokens"] = prompt_tokens
        if completion_tokens is not None:
            token_metrics["output_tokens"] = completion_tokens
        if prompt_tokens is not None and completion_tokens is not None:
            token_metrics["total_tokens"] = prompt_tokens + completion_tokens

        if span.get_tag("bedrock.request.temperature"):
            expected_parameters = {"temperature": float(span.get_tag("bedrock.request.temperature"))}
        if span.get_tag("bedrock.request.max_tokens"):
            expected_parameters["max_tokens"] = int(span.get_tag("bedrock.request.max_tokens"))
        if span.get_tag("bedrock.request.top_p"):
            expected_parameters["top_p"] = float(span.get_tag("bedrock.request.top_p"))
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
            token_metrics=token_metrics,
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @classmethod
    def _test_llmobs_invoke(cls, provider, bedrock_client, mock_llmobs_span_writer, cassette_name=None, n_output=1):
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin._override(bedrock_client, tracer=mock_tracer)
        # Need to disable and re-enable LLMObs service to use the mock tracer
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)  # only want botocore patched

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
            if provider == "anthropic_message":
                # we do this to reuse a cassette which tests
                # cross-region inference
                model = "us." + model
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
        pin._override(bedrock_client, tracer=mock_tracer)
        # Need to disable and re-enable LLMObs service to use the mock tracer
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)  # only want botocore patched

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

    def test_llmobs_ai21_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("ai21", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_amazon_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("amazon", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("anthropic", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_message(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("anthropic_message", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_cohere_single_output_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke(
            "cohere", bedrock_client, mock_llmobs_span_writer, cassette_name="cohere_invoke_single_output.yaml"
        )

    def test_llmobs_cohere_multi_output_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke("meta", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_amazon_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("amazon", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("anthropic", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_anthropic_message_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer
    ):
        self._test_llmobs_invoke_stream("anthropic_message", bedrock_client, mock_llmobs_span_writer)

    def test_llmobs_cohere_single_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_stream_single_output.yaml",
        )

    def test_llmobs_cohere_multi_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_llmobs_span_writer,
            cassette_name="cohere_invoke_stream_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer):
        self._test_llmobs_invoke_stream("meta", bedrock_client, mock_llmobs_span_writer)

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse(cls, bedrock_client, mock_llmobs_span_writer, request_vcr, mock_tracer, llmobs_events):
        system_content = "You are an expert swe that is to use the tool fetch_concept"
        user_content = "Explain the concept of distributed tracing in a simple way"
        tools = [
            {
                "toolSpec": {
                    "name": "fetch_concept",
                    "description": "Fetch an expert explanation for a concept",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"concept": {"type": "string", "description": "The concept to explain"}},
                            "required": ["concept"],
                        }
                    },
                }
            }
        ]

        request_params = create_bedrock_converse_request(user_message=user_content, tools=tools, system=system_content)

        with request_vcr.use_cassette("bedrock_converse.yaml"):
            response = bedrock_client.converse(**request_params)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        llmobs_events[0] = _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            output_messages=[
                {
                    "role": "assistant",
                    "content": response["output"]["message"]["content"][0]["text"],
                    "tool_calls": [
                        {
                            "arguments": {"concept": "distributed tracing"},
                            "name": "fetch_concept",
                            "tool_id": mock.ANY,
                        }
                    ],
                }
            ],
            metadata={
                "stop_reason": "tool_use",
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "top_p": request_params.get("inferenceConfig", {}).get("topP"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            token_metrics={
                "input_tokens": response["usage"]["inputTokens"],
                "output_tokens": response["usage"]["outputTokens"],
                "total_tokens": response["usage"]["totalTokens"],
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_error(
        self, bedrock_client, mock_llmobs_span_writer, request_vcr, mock_tracer, llmobs_events
    ):
        """Test error handling for the Bedrock Converse API."""
        import botocore

        user_content = "Explain the concept of distributed tracing in a simple way"
        request_params = create_bedrock_converse_request(user_message=user_content)

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("bedrock_converse_error.yaml"):
                bedrock_client.converse(**request_params)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[{"role": "user", "content": "Explain the concept of distributed tracing in a simple way"}],
            output_messages=[{"content": ""}],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature", 0.0),
                "top_p": request_params.get("inferenceConfig", {}).get("topP", 0.0),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens", 0),
            },
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream(
        cls, bedrock_client, mock_llmobs_span_writer, request_vcr, mock_tracer, llmobs_events
    ):
        """Documents behavior for streaming (currently unsupported)"""
        user_content = "Explain the concept of distributed tracing in a simple way"
        request_params = create_bedrock_converse_request(user_message=user_content)

        with request_vcr.use_cassette("bedrock_converse_stream.yaml"):
            response = bedrock_client.converse_stream(**request_params)
            # some dummy code to verify stream structure isn't broken
            for chunk in response["stream"]:
                if "contentBlockDelta" in chunk:
                    chunk["contentBlockDelta"]["delta"]["text"]

        assert len(llmobs_events) == 0

    def test_llmobs_error(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer, request_vcr):
        import botocore

        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin._override(bedrock_client, tracer=mock_tracer)
        # Need to disable and re-enable LLMObs service to use the mock tracer
        LLMObs.disable()
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)  # only want botocore patched
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
                        "top_p": float(span.get_tag("bedrock.request.top_p")),
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
