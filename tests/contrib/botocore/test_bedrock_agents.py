import pytest

from tests.contrib.botocore.bedrock_utils import AGENT_ALIAS_ID
from tests.contrib.botocore.bedrock_utils import AGENT_ID
from tests.contrib.botocore.bedrock_utils import AGENT_INPUT
from tests.contrib.botocore.bedrock_utils import BOTO_VERSION


@pytest.mark.snapshot
def test_agent_invoke(bedrock_agent_client, request_vcr):
    with request_vcr.use_cassette("agent_invoke.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=True,
            inputText=AGENT_INPUT,
        )
        for _ in response["completion"]:
            pass


@pytest.mark.skipif(BOTO_VERSION < (1, 36, 0), reason="Streaming configurations not supported in this boto3 version")
@pytest.mark.snapshot(token="tests.contrib.botocore.test_bedrock_agents.test_agent_invoke")
def test_agent_invoke_stream(bedrock_agent_client, request_vcr):
    with request_vcr.use_cassette("agent_invoke.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=True,
            inputText=AGENT_INPUT,
            streamingConfigurations={"applyGuardrailInterval": 50, "streamFinalResponse": True},
        )
        for _ in response["completion"]:
            pass


def test_span_finishes_after_generator_exit(bedrock_agent_client, request_vcr, mock_tracer_agent):
    with request_vcr.use_cassette("agent_invoke.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=True,
            inputText=AGENT_INPUT,
        )
        i = 0
        with pytest.raises(GeneratorExit):
            for _ in response["completion"]:
                i += 1
                if i >= 6:
                    raise GeneratorExit
        span = mock_tracer_agent.pop_traces()[0][0]
        assert span is not None
        assert span.name == "Bedrock Agent {}".format(AGENT_ID)
        assert span.resource == "aws.bedrock-agent-runtime"


def test_agent_invoke_trace_disabled(bedrock_agent_client, request_vcr, mock_tracer_agent):
    # Test that we still get the agent span when enableTrace is set to False
    with request_vcr.use_cassette("agent_invoke_trace_disabled.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=False,
            inputText=AGENT_INPUT,
        )
        for _ in response["completion"]:
            pass
    trace = mock_tracer_agent.pop_traces()[0]
    assert len(trace) == 1
    span = trace[0]
    assert span.name == "Bedrock Agent {}".format(AGENT_ID)
    assert span.resource == "aws.bedrock-agent-runtime"


@pytest.mark.skipif(BOTO_VERSION < (1, 36, 0), reason="Streaming configurations not supported in this boto3 version")
def test_agent_invoke_stream_trace_disabled(bedrock_agent_client, request_vcr, mock_tracer_agent):
    # Test that we still get the agent span when enableTrace is set to False
    with request_vcr.use_cassette("agent_invoke_trace_disabled.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=False,
            inputText=AGENT_INPUT,
            streamingConfigurations={"applyGuardrailInterval": 50, "streamFinalResponse": True},
        )
        for _ in response["completion"]:
            pass
    trace = mock_tracer_agent.pop_traces()[0]
    assert len(trace) == 1
    span = trace[0]
    assert span.name == "Bedrock Agent {}".format(AGENT_ID)
    assert span.resource == "aws.bedrock-agent-runtime"
