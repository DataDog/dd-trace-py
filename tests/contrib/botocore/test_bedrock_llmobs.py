from io import BytesIO
import json
import os

import botocore
import mock
from mock import patch as mock_patch
import pytest
import urllib3

from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.contrib.botocore.bedrock_utils import _MOCK_RESPONSE_DATA
from tests.contrib.botocore.bedrock_utils import _MODELS
from tests.contrib.botocore.bedrock_utils import _REQUEST_BODIES
from tests.contrib.botocore.bedrock_utils import BOTO_VERSION
from tests.contrib.botocore.bedrock_utils import bedrock_converse_args_with_system_and_tool
from tests.contrib.botocore.bedrock_utils import create_bedrock_converse_request
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer
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
def boto3(aws_credentials, llmobs_span_writer, ddtrace_global_config):
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
def bedrock_client_proxy(boto3):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    bedrock_client = session.client("bedrock-runtime", endpoint_url="http://0.0.0.0:4000")
    yield bedrock_client


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def mock_tracer(bedrock_client):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(bedrock_client)
    pin._override(bedrock_client, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def mock_tracer_proxy(bedrock_client_proxy):
    mock_tracer = DummyTracer()
    pin = Pin.get_from(bedrock_client_proxy)
    pin._override(bedrock_client_proxy, tracer=mock_tracer)
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


@pytest.fixture
def mock_invoke_model_http():
    yield botocore.awsrequest.AWSResponse("fake-url", 200, [], None)


@pytest.fixture
def mock_invoke_model_http_error():
    yield botocore.awsrequest.AWSResponse("fake-url", 403, [], None)


@pytest.fixture
def mock_invoke_model_response():
    yield {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "date": "Wed, 05 Mar 2025 18:13:31 GMT",
                "content-type": "application/json",
                "content-length": "285",
                "connection": "keep-alive",
                "x-amzn-requestid": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
                "x-amzn-bedrock-invocation-latency": "2823",
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": botocore.response.StreamingBody(
            urllib3.response.HTTPResponse(
                body=BytesIO(_MOCK_RESPONSE_DATA),
                status=200,
                headers={"Content-Type": "application/json"},
                preload_content=False,
            ),
            291,
        ),
    }


@pytest.fixture
def mock_invoke_model_response_error():
    yield {
        "Error": {
            "Message": "The security token included in the request is expired",
            "Code": "ExpiredTokenException",
        },
        "ResponseMetadata": {
            "RequestId": "b1c68b9a-552a-466b-b761-4ee6b710ece4",
            "HTTPStatusCode": 403,
            "HTTPHeaders": {
                "date": "Wed, 05 Mar 2025 21:45:12 GMT",
                "content-type": "application/json",
                "content-length": "67",
                "connection": "keep-alive",
                "x-amzn-requestid": "b1c68b9a-552a-466b-b761-4ee6b710ece4",
                "x-amzn-errortype": "ExpiredTokenException:http://internal.amazon.com/coral/com.amazon.coral.service/",
            },
            "RetryAttempts": 0,
        },
    }


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
    def _test_llmobs_invoke_proxy(
        cls,
        provider,
        bedrock_client,
        mock_tracer,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
        n_output=1,
    ):
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
        # mock out the completions response
        with mock_patch.object(bedrock_client, "_make_request") as mock_invoke_model_call:
            mock_invoke_model_call.return_value = mock_invoke_model_http, mock_invoke_model_response
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())

        assert len(mock_tracer.pop_traces()[0]) == 1
        assert len(llmobs_events) == 0
        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke_stream_proxy(
        cls,
        provider,
        bedrock_client,
        mock_tracer,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
        n_output=1,
    ):
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
        # mock out the completions response
        with mock_patch.object(bedrock_client, "_make_request") as mock_invoke_model_call:
            mock_invoke_model_call.return_value = mock_invoke_model_http, mock_invoke_model_response
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model(body=body, modelId=model)
            for _ in response.get("body"):
                pass

        assert len(mock_tracer.pop_traces()[0]) == 1
        assert len(llmobs_events) == 0
        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke(cls, provider, bedrock_client, mock_tracer, llmobs_events, cassette_name=None, n_output=1):
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

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == cls.expected_llmobs_span_event(span, n_output, message="message" in provider)
        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke_stream(
        cls, provider, bedrock_client, mock_tracer, llmobs_events, cassette_name=None, n_output=1
    ):
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

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == cls.expected_llmobs_span_event(span, n_output, message="message" in provider)

    def test_llmobs_ai21_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "ai21",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_amazon_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "amazon",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_anthropic_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "anthropic",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_anthropic_message_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "anthropic_message",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_cohere_single_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_cohere_multi_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
            n_output=2,
        )

    def test_llmobs_meta_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_proxy(
            "meta",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_amazon_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "amazon",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_anthropic_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "anthropic",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_anthropic_message_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "anthropic_message",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_cohere_single_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_cohere_multi_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
        mock_invoke_model_response,
    ):
        self._test_llmobs_invoke_stream_proxy(
            "meta",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            mock_invoke_model_response,
        )

    def test_llmobs_error_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http_error,
        mock_invoke_model_response_error,
    ):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            # mock out the completions response
            with mock_patch.object(bedrock_client_proxy, "_make_request") as mock_invoke_model_call:
                mock_invoke_model_call.return_value = mock_invoke_model_http_error, mock_invoke_model_response_error
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client_proxy.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())

        assert len(mock_tracer_proxy.pop_traces()[0]) == 1
        assert len(llmobs_events) == 0
        LLMObs.disable()

    def test_llmobs_ai21_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("ai21", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_amazon_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("amazon", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("anthropic", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_message(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("anthropic_message", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_cohere_single_output_invoke(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke(
            "cohere", bedrock_client, mock_tracer, llmobs_events, cassette_name="cohere_invoke_single_output.yaml"
        )

    def test_llmobs_cohere_multi_output_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("meta", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_amazon_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("amazon", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("anthropic", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_message_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream("anthropic_message", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_cohere_single_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_stream_single_output.yaml",
        )

    def test_llmobs_cohere_multi_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_stream_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("meta", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_only_patches_bedrock(self, ddtrace_global_config, llmobs_span_writer):
        llmobs_service.disable()

        with override_global_config(
            {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.llmobs"}
        ):
            llmobs_service.enable(integrations_enabled=True)
            mock_tracer = DummyTracer()
            # ensure we don't get spans for non-bedrock services
            from botocore.exceptions import ClientError
            import botocore.session

            session = botocore.session.get_session()
            sqs_client = session.create_client("sqs", region_name="us-east-1")
            pin = Pin.get_from(sqs_client)
            pin._override(sqs_client, tracer=mock_tracer)
            try:
                sqs_client.list_queues()
            except ClientError:
                pass
            assert mock_tracer.pop_traces() == []

        llmobs_service.disable()

    def test_llmobs_error(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events, request_vcr):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("meta_invoke_error.yaml"):
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
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

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse(cls, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse.yaml"):
            response = bedrock_client.converse(**request_params)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": request_params.get("system")[0]["text"]},
                {"role": "user", "content": request_params.get("messages")[0].get("content")[0].get("text")},
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
    def test_llmobs_converse_error(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
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
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens", 0),
            },
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream(cls, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        output_msg = ""
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse_stream.yaml"):
            response = bedrock_client.converse_stream(**request_params)
            for chunk in response["stream"]:
                if "contentBlockDelta" in chunk and "delta" in chunk["contentBlockDelta"]:
                    if "text" in chunk["contentBlockDelta"]["delta"]:
                        output_msg += chunk["contentBlockDelta"]["delta"]["text"]

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": request_params.get("system")[0]["text"]},
                {"role": "user", "content": request_params.get("messages")[0].get("content")[0].get("text")},
            ],
            output_messages=[
                {
                    "role": "assistant",
                    "content": output_msg,
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
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            token_metrics={
                "input_tokens": 259,
                "output_tokens": 64,
                "total_tokens": 323,
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )
