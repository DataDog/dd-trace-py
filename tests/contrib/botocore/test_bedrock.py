import json
import os
import sys

import mock
import pytest
import vcr

from ddtrace import Pin
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import DummyTracer
from tests.utils import DummyWriter


pytest.mark.skipif(sys.version_info < (3, 8), reason="vcrpy does not support Python 3.7 or older")


_MODELS = {
    "ai21": "ai21.j2-mid-v1",
    "amazon": "amazon.titan-tg1-large",
    "anthropic": "anthropic.claude-instant-v1",
    "cohere": "cohere.command-light-text-v14",
    "meta": "meta.llama2-13b-chat-v1",
}

_REQUEST_BODIES = {
    "ai21": json.dumps(
        {
            "prompt": "Explain like I'm a five-year old: what is a neural network?",
            "temperature": 0.9,
            "topP": 1.0,
            "maxTokens": 10,
            "stopSequences": [],
        }
    ),
    "amazon": json.dumps(
        {
            "inputText": "Command: can you explain what Datadog is to someone not in the tech industry?",
            "textGenerationConfig": {"maxTokenCount": 50, "stopSequences": [], "temperature": 0, "topP": 0.9},
        }
    ),
    "anthropic": json.dumps(
        {
            "prompt": "\n\nHuman: %s\n\nAssistant: What makes you better than Chat-GPT or LLAMA?",
            "temperature": 0.9,
            "top_p": 1,
            "top_k": 250,
            "max_tokens_to_sample": 50,
            "stop_sequences": ["\n\nHuman:"],
        }
    ),
    "cohere": json.dumps(
        {
            "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
            "temperature": 0.9,
            "p": 1.0,
            "k": 0,
            "max_tokens": 10,
            "stop_sequences": [],
            "stream": False,
            "num_generations": 1,
        }
    ),
    "meta": json.dumps(
        {
            "prompt": "What does 'lorem ipsum' mean?",
            "temperature": 0.9,
            "top_p": 1.0,
            "max_gen_len": 60,
        }
    ),
}


# VCR is used to capture and store network requests made to OpenAI and other APIs.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
# To (re)-generate the cassettes: pass a real API key with
# {PROVIDER}_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "bedrock_cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "X-Amz-Security-Token"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()


@pytest.fixture
def botocore():
    patch()
    import botocore

    yield botocore
    unpatch()


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def boto3(botocore, aws_credentials):
    import boto3

    yield boto3


@pytest.fixture
def bedrock_client(boto3, botocore, request_vcr):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    bedrock_client = session.client("bedrock-runtime")
    yield bedrock_client


class TestBedrockConfig(SubprocessTestCase):
    def setUp(self):
        patch()
        import boto3

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
        with get_request_vcr().use_cassette("meta_invoke.yaml"):
            response = self.bedrock_client.invoke_model(body=_REQUEST_BODIES["meta"], modelId=_MODELS["meta"])
            json.loads(response.get("body").read())
        span = self.mock_tracer.pop_traces()[0][0]
        assert span.resource == "InvokeModel"
        assert span.service == "test-svc"
        assert span.get_tag("env") == "staging"
        assert span.get_tag("version") == "1234"

    def _test_span_sampling(self, rate):
        num_completions = 200
        for _ in range(num_completions):
            with get_request_vcr().use_cassette("meta_invoke.yaml"):
                response = self.bedrock_client.invoke_model(body=_REQUEST_BODIES["meta"], modelId=_MODELS["meta"])
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
    with request_vcr.use_cassette("ai21_invoke.yaml"):
        body, model = _REQUEST_BODIES["ai21"], _MODELS["ai21"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_amazon_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("amazon_invoke.yaml"):
        body, model = _REQUEST_BODIES["amazon"], _MODELS["amazon"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_anthropic_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("anthropic_invoke.yaml"):
        body, model = _REQUEST_BODIES["anthropic"], _MODELS["anthropic"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_cohere_invoke_single_output(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_single_output.yaml"):
        body, model = _REQUEST_BODIES["cohere"], _MODELS["cohere"]
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
    with request_vcr.use_cassette("meta_invoke.yaml"):
        body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        json.loads(response.get("body").read())


@pytest.mark.snapshot
def test_amazon_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("amazon_invoke_stream.yaml"):
        body, model = _REQUEST_BODIES["amazon"], _MODELS["amazon"]
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_anthropic_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("anthropic_invoke_stream.yaml"):
        body, model = _REQUEST_BODIES["anthropic"], _MODELS["anthropic"]
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_cohere_invoke_stream_single_output(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_stream_single_output.yaml"):
        body, model = _REQUEST_BODIES["cohere"], _MODELS["cohere"]
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_cohere_invoke_stream_multiple_output(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_stream_multiple_output.yaml"):
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
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=_MODELS["cohere"])
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_meta_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke_stream.yaml"):
        body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot
def test_auth_error(bedrock_client, request_vcr):
    import botocore

    with pytest.raises(botocore.exceptions.ClientError):
        with request_vcr.use_cassette("meta_invoke_error.yaml"):
            body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
            bedrock_client.invoke_model(body=body, modelId=model)


@pytest.mark.snapshot(token="tests.contrib.botocore.test_bedrock.test_read_error")
def test_read_error(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke.yaml"):
        body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        with mock.patch("ddtrace.contrib.botocore.services.bedrock._extract_response") as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                response.get("body").read()


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_read_stream_error(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke_stream.yaml"):
        body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
        response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
        with mock.patch(
            "ddtrace.contrib.botocore.services.bedrock._extract_streamed_response"
        ) as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                for _ in response.get("body"):
                    pass


@pytest.mark.snapshot
def test_readlines_error(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke.yaml"):
        body, model = _REQUEST_BODIES["meta"], _MODELS["meta"]
        response = bedrock_client.invoke_model(body=body, modelId=model)
        with mock.patch("ddtrace.contrib.botocore.services.bedrock._extract_response") as mock_extract_response:
            mock_extract_response.side_effect = Exception("test")
            with pytest.raises(Exception):
                response.get("body").readlines()
