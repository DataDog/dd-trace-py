import os

import pytest

from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import unpatch
from ddtrace.contrib.internal.urllib3.patch import patch as urllib3_patch
from ddtrace.contrib.internal.urllib3.patch import unpatch as urllib3_unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import TestLLMObsSpanWriter
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
    # os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    # os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    # os.environ["AWS_SECURITY_TOKEN"] = "testing"
    # os.environ["AWS_SESSION_TOKEN"] = "testing"
    # os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    pass


@pytest.fixture
def mock_tracer(bedrock_client):
    pin = Pin.get_from(bedrock_client)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(bedrock_client, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def mock_tracer_agent(bedrock_agent_client):
    pin = Pin.get_from(bedrock_agent_client)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(bedrock_agent_client, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def boto3(aws_credentials, llmobs_span_writer, ddtrace_global_config):
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
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


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
