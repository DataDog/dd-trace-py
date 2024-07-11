import json
import os

import mock
import pytest

from ddtrace import Pin
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


vcr = pytest.importorskip("vcr")


_MODELS = {
    "ai21": "ai21.j2-mid-v1",
    "amazon": "amazon.titan-tg1-large",
    "anthropic": "anthropic.claude-instant-v1",
    "anthropic_message": "anthropic.claude-3-sonnet-20240229-v1:0",
    "cohere": "cohere.command-light-text-v14",
    "meta": "meta.llama2-13b-chat-v1",
}

_REQUEST_BODIES = {
    "ai21": {
        "prompt": "Explain like I'm a five-year old: what is a neural network?",
        "temperature": 0.9,
        "topP": 1.0,
        "maxTokens": 10,
        "stopSequences": [],
    },
    "amazon": {
        "inputText": "Command: can you explain what Datadog is to someone not in the tech industry?",
        "textGenerationConfig": {"maxTokenCount": 50, "stopSequences": [], "temperature": 0, "topP": 0.9},
    },
    "anthropic": {
        "prompt": "\n\nHuman: %s\n\nAssistant: What makes you better than Chat-GPT or LLAMA?",
        "temperature": 0.9,
        "top_p": 1,
        "top_k": 250,
        "max_tokens_to_sample": 50,
        "stop_sequences": ["\n\nHuman:"],
    },
    "anthropic_message": {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "summarize the plot to the lord of the rings in a dozen words"}],
            }
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 50,
        "temperature": 0,
    },
    "cohere": {
        "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
        "temperature": 0.9,
        "p": 1.0,
        "k": 0,
        "max_tokens": 10,
        "stop_sequences": [],
        "stream": False,
        "num_generations": 1,
    },
    "meta": {
        "prompt": "What does 'lorem ipsum' mean?",
        "temperature": 0.9,
        "top_p": 1.0,
        "max_gen_len": 60,
    },
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
def ddtrace_global_config():
    config = {}
    return config


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def ddtrace_config_botocore():
    return {}


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials. To regenerate test cassettes, comment this out and use real credentials."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def boto3(aws_credentials, mock_llmobs_span_writer, ddtrace_global_config, ddtrace_config_botocore):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("botocore", ddtrace_config_botocore):
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
    LLMObs.disable()


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


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
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
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        assert mock_llmobs_span_writer.enqueue.call_count == 1
        mock_llmobs_span_writer.enqueue.assert_called_with(
            cls.expected_llmobs_span_event(span, n_output, message="message" in provider)
        )

    @classmethod
    def _test_llmobs_invoke_stream(
        cls, provider, bedrock_client, mock_llmobs_span_writer, cassette_name=None, n_output=1
    ):
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin.override(bedrock_client, tracer=mock_tracer)
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

    def test_llmobs_error(self, ddtrace_global_config, bedrock_client, mock_llmobs_span_writer, request_vcr):
        import botocore

        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin = Pin.get_from(bedrock_client)
        pin.override(bedrock_client, tracer=mock_tracer)
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
