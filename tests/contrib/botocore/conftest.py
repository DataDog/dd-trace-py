import os

import botocore
import mock
import pytest

from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import unpatch
from ddtrace.contrib.internal.urllib3.patch import patch as urllib3_patch
from ddtrace.contrib.internal.urllib3.patch import unpatch as urllib3_unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import TestLLMObsSpanWriter
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
def boto3(aws_credentials, ddtrace_global_config):
    global_config = {"_dd_api_key": "<not-a-real-api_key>"}
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        urllib3_unpatch()
        patch()
        import boto3

        yield boto3
        unpatch()
        urllib3_patch()


@pytest.fixture
def bedrock_client(boto3, request_vcr):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    client = session.client("bedrock-runtime")
    yield client


@pytest.fixture
def bedrock_agent_client(boto3, request_vcr):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    client = session.client("bedrock-agent-runtime")
    yield client


@pytest.fixture
def bedrock_client_proxy(boto3):
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    bedrock_client = session.client("bedrock-runtime", endpoint_url="http://localhost:4000")
    yield bedrock_client


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def bedrock_llmobs(tracer, monkeypatch):
    monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False, agentless_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def bedrock_agents_llmobs(tracer, llmobs_span_writer):
    """LLMObs fixture for bedrock_agents tests.

    Keeps the real ``TestLLMObsSpanWriter`` (instead of a mock) because the
    bedrock_agents integration synthesizes span events without a backing APM
    span — the enqueued events are not derivable from ``meta_struct`` and must
    be read out of the writer via ``llmobs_events``.
    """
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False, agentless_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = llmobs_span_writer
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def llmobs_events(llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture
def mock_invoke_model_http():
    yield botocore.awsrequest.AWSResponse("fake-url", 200, [], None)


@pytest.fixture
def mock_invoke_model_http_error():
    yield botocore.awsrequest.AWSResponse("fake-url", 403, [], None)


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
