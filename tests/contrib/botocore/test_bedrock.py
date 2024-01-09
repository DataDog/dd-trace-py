import json
import os
import pytest

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
from tests.utils import TracerTestCase
from tests.utils import override_global_config


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
def boto3(botocore):
    import boto3
    yield boto3


@pytest.fixture
def mock_tracer():
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    yield mock_tracer


@pytest.fixture
def ddtrace_config():
    return {}


@pytest.fixture
def bedrock_client(boto3, botocore, request_vcr, ddtrace_config):
    with override_global_config(ddtrace_config):
        session = boto3.Session(
            profile_name="601427279990_account-admin",
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
            profile_name="601427279990_account-admin",
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
        env_overrides=dict(DD_SERVICE="test-svc", DD_ENV="staging", DD_VERSION="1234", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_global_tags(self):
        with get_request_vcr().use_cassette("meta_invoke.yaml"):
            body = json.dumps(
                {
                    "prompt": "What does 'lorem ipsum' mean?",
                    "temperature": 0.9,
                    "top_p": 1.0,
                    "max_gen_len": 60,
                }
            )
            response = self.bedrock_client.invoke_model(
                body=body, modelId="meta.llama2-13b-chat-v1", accept="application/json", contentType="application/json"
            )
            json.loads(response.get("body").read())
        span = self.mock_tracer.pop_traces()[0][0]
        assert span.resource == "InvokeModel"
        assert span.service == "test-svc"
        assert span.get_tag("env") == "staging"
        assert span.get_tag("version") == "1234"


@pytest.mark.snapshot()
def test_ai21_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("ai21_invoke.yaml"):
        body = json.dumps(
            {
                "prompt": "Explain like I'm a five-year old: what is a neural network?",
                "temperature": 0.9,
                "topP": 1.0,
                "maxTokens": 10,
                "stopSequences": [],
            }
        )
        response = bedrock_client.invoke_model(
            body=body, modelId="ai21.j2-mid-v1", accept="application/json", contentType="application/json"
        )
        json.loads(response.get("body").read())


@pytest.mark.snapshot()
def test_amazon_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("amazon_invoke.yaml"):
        body = json.dumps(
            {
                "inputText": "Command: can you explain what Datadog is to someone not in the tech industry?",
                "textGenerationConfig": {"maxTokenCount": 50, "stopSequences": [], "temperature": 0, "topP": 0.9},
            }
        )
        response = bedrock_client.invoke_model(
            body=body, modelId="amazon.titan-tg1-large", accept="application/json", contentType="application/json"
        )
        json.loads(response.get("body").read())


@pytest.mark.snapshot()
def test_anthropic_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("anthropic_invoke.yaml"):
        body = json.dumps(
            {
                "prompt": "\n\nHuman: %s\n\nAssistant: What makes you better than Chat-GPT or LLAMA?",
                "temperature": 0.9,
                "top_p": 1,
                "top_k": 250,
                "max_tokens_to_sample": 50,
                "stop_sequences": ["\n\nHuman:"],
            }
        )
        response = bedrock_client.invoke_model(
            body=body, modelId="anthropic.claude-instant-v1", accept="application/json", contentType="application/json"
        )
        json.loads(response.get("body").read())


@pytest.mark.snapshot()
def test_cohere_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke.yaml"):
        body = json.dumps(
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
        )
        response = bedrock_client.invoke_model(
            body=body, modelId="cohere.command-light-text-v14", accept="application/json", contentType="application/json"
        )
        json.loads(response.get("body").read())


@pytest.mark.snapshot()
def test_meta_invoke(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke.yaml"):
        body = json.dumps(
            {
                "prompt": "What does 'lorem ipsum' mean?",
                "temperature": 0.9,
                "top_p": 1.0,
                "max_gen_len": 60,
            }
        )
        response = bedrock_client.invoke_model(
            body=body, modelId="meta.llama2-13b-chat-v1", accept="application/json", contentType="application/json"
        )
        json.loads(response.get("body").read())


@pytest.mark.snapshot()
def test_amazon_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("amazon_invoke_stream.yaml"):
        body = json.dumps(
            {
                "inputText": "Command: can you explain what Datadog is to someone not in the tech industry?",
                "textGenerationConfig": {"maxTokenCount": 50, "stopSequences": [], "temperature": 0, "topP": 0.9},
            }
        )
        response = bedrock_client.invoke_model_with_response_stream(
            body=body, modelId="amazon.titan-tg1-large", accept="application/json", contentType="application/json"
        )
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot()
def test_anthropic_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("anthropic_invoke_stream.yaml"):
        body = json.dumps(
            {
                "prompt": "\n\nHuman: %s\n\nAssistant: What makes you better than Chat-GPT or LLAMA?",
                "temperature": 0.9,
                "top_p": 1,
                "top_k": 250,
                "max_tokens_to_sample": 50,
                "stop_sequences": ["\n\nHuman:"],
            }
        )
        response = bedrock_client.invoke_model_with_response_stream(
            body=body, modelId="anthropic.claude-instant-v1", accept="application/json", contentType="application/json"
        )
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot()
def test_cohere_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("cohere_invoke_stream.yaml"):
        body = json.dumps(
            {
                "prompt": "What does 'lorem ipsum' mean?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": False,
                "num_generations": 2,
            }
        )
        response = bedrock_client.invoke_model_with_response_stream(
            body=body, modelId="cohere.command-light-text-v14", accept="application/json", contentType="application/json"
        )
        for _ in response.get("body"):
            pass


@pytest.mark.snapshot()
def test_meta_invoke_stream(bedrock_client, request_vcr):
    with request_vcr.use_cassette("meta_invoke_stream.yaml"):
        body = json.dumps(
            {
                "prompt": "What does 'lorem ipsum' mean?",
                "temperature": 0.9,
                "top_p": 1.0,
                "max_gen_len": 5,
            }
        )
        response = bedrock_client.invoke_model_with_response_stream(
            body=body, modelId="meta.llama2-13b-chat-v1", accept="application/json", contentType="application/json"
        )
        for _ in response.get("body"):
            pass
