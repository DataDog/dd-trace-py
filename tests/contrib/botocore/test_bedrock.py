import json
import os

import mock
import pytest

from ddtrace import Pin
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
from tests.contrib.botocore.bedrock_utils import _MODELS
from tests.contrib.botocore.bedrock_utils import _REQUEST_BODIES
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


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


class TestBedrockConfig(SubprocessTestCase):
    def setUp(self):
        patch()
        import boto3

        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        self.session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
        self.bedrock_client = self.session.client("bedrock-runtime")
        self.mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(self.bedrock_client)
        pin.override(self.bedrock_client, tracer=self.mock_tracer)

        super(TestBedrockConfig, self).setUp()

    @run_in_subprocess(
        env_overrides=dict(
            DD_SERVICE="test-svc", DD_ENV="staging", DD_VERSION="1234", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"
        )
    )
    def test_global_tags(self):
        body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
        with get_request_vcr().use_cassette("meta_invoke.yaml"):
            response = self.bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())
        span = self.mock_tracer.pop_traces()[0][0]
        assert span.resource == "InvokeModel"
        assert span.service == "test-svc"
        assert span.get_tag("env") == "staging"
        assert span.get_tag("version") == "1234"

    def _test_span_sampling(self, rate):
        body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
        num_completions = 200
        for _ in range(num_completions):
            with get_request_vcr().use_cassette("meta_invoke.yaml"):
                response = self.bedrock_client.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())
        traces = self.mock_tracer.pop_traces()
        sampled = 0
        for trace in traces:
            for span in trace:
                if span.get_tag("bedrock.response.choices.0.text"):
                    sampled += 1
        assert (rate * num_completions - 30) < sampled < (rate * num_completions + 30)

    @run_in_subprocess(env_overrides=dict(DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE="0.0"))
    def test_span_sampling_0(self):
        self._test_span_sampling(rate=float(os.getenv("DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")))

    @run_in_subprocess(env_overrides=dict(DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE="0.25"))
    def test_span_sampling_25(self):
        self._test_span_sampling(rate=float(os.getenv("DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")))

    @run_in_subprocess(env_overrides=dict(DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE="0.75"))
    def test_span_sampling_75(self):
        self._test_span_sampling(rate=float(os.getenv("DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")))

    @run_in_subprocess(env_overrides=dict(DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE="1.0"))
    def test_span_sampling_100(self):
        self._test_span_sampling(rate=float(os.getenv("DD_BEDROCK_SPAN_PROMPT_COMPLETION_SAMPLE_RATE")))


@pytest.mark.snapshot
def test_ai21_invoke(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["ai21"]), _MODELS["ai21"]
    with request_vcr.use_cassette("ai21_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_amazon_invoke(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["amazon"]), _MODELS["amazon"]
    with request_vcr.use_cassette("amazon_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_anthropic_invoke(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["anthropic"]), _MODELS["anthropic"]
    with request_vcr.use_cassette("anthropic_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_anthropic_message_invoke(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["anthropic_message"]), _MODELS["anthropic_message"]
    with request_vcr.use_cassette("anthropic_message_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_cohere_invoke_single_output(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["cohere"]), _MODELS["cohere"]
    with request_vcr.use_cassette("cohere_invoke_single_output.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_cohere_invoke_multi_output(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_multi_output.yaml"):
        body = json.dumps(
            {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": False,
                "num_generations": 2,
            }
        )
        response = bedrock_client.invoke_model(body=body, modelId=_MODELS["cohere"])
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_meta_invoke(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with get_request_vcr().use_cassette("meta_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_amazon_invoke_stream(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["amazon"]), _MODELS["amazon"]
    with request_vcr.use_cassette("amazon_invoke_stream.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_anthropic_invoke_stream(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["anthropic"]), _MODELS["anthropic"]
    with request_vcr.use_cassette("anthropic_invoke_stream.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_anthropic_message_invoke_stream(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["anthropic_message"]), _MODELS["anthropic_message"]
    with request_vcr.use_cassette("anthropic_message_invoke_stream.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_cohere_invoke_stream_single_output(bedrock_client, request_vcr):
    body = json.dumps(
        {
            "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
            "temperature": 0.9,
            "p": 1.0,
            "k": 0,
            "max_tokens": 10,
            "stop_sequences": [],
            "stream": True,
            "num_generations": 1,
        }
    )
    model = _MODELS["cohere"]
    with request_vcr.use_cassette("cohere_invoke_stream_single_output.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_cohere_invoke_stream_multi_output(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_stream_multi_output.yaml"):
        body = json.dumps(
            {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": True,
                "num_generations": 2,
            }
        )
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=_MODELS["cohere"])
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_meta_invoke_stream(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with request_vcr.use_cassette("meta_invoke_stream.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_auth_error(bedrock_client, request_vcr):
    import botocore

    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with pytest.raises(botocore.exceptions.ClientError):
        with request_vcr.use_cassette("meta_invoke_error.yaml"):
            bedrock_client.invoke_model(body=body, modelId=model)


@pytest.mark.snapshot(token="tests.contrib.botocore.test_bedrock.test_read_error", ignores=["meta.error.stack"])
def test_read_error(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with request_vcr.use_cassette("meta_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        with mock.patch(
            "ddtrace.contrib.botocore.services.bedrock._extract_text_and_response_reason"
        ) as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                response.get("body").read()


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_read_stream_error(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with request_vcr.use_cassette("meta_invoke_stream.yaml"):
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        with mock.patch(
            "ddtrace.contrib.botocore.services.bedrock._extract_streamed_response"
        ) as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                for _ in response.get("body"):
                    pass


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_readlines_error(bedrock_client, request_vcr):
    body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
    with request_vcr.use_cassette("meta_invoke.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        with mock.patch(
            "ddtrace.contrib.botocore.services.bedrock._extract_text_and_response_reason"
        ) as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                response.get("body").readlines()


@pytest.mark.snapshot
def test_amazon_embedding(bedrock_client, request_vcr):
    body = json.dumps({"inputText": "Hello World!"})
    model = "amazon.titan-embed-text-v1"
    with request_vcr.use_cassette("amazon_embedding.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_cohere_embedding(bedrock_client, request_vcr):
    body = json.dumps({"texts": ["Hello World!", "Goodbye cruel world!"], "input_type": "search_document"})
    model = "cohere.embed-english-v3"
    with request_vcr.use_cassette("cohere_embedding.yaml"):
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())
