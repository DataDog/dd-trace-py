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
    """Snapshot with LLMObs disabled."""
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


@pytest.mark.skipif(BOTO_VERSION < (1, 38, 0), reason="LLMObs bedrock agent step spans require boto3 > 1.36.0")
@pytest.mark.snapshot(
    ignores=[
        "meta.llmobs_trace_id",
        "meta.llmobs_parent_id",
        "meta._dd.p.tid",
        "meta._dd.p.llmobs_ml_app",
        "meta._dd.p.llmobs_parent_id",
        "meta._dd.p.llmobs_sd",
        "meta._dd.p.llmobs_sr",
        "meta_struct",
    ]
)
def test_agent_invoke_with_step_spans(bedrock_agent_client, request_vcr, bedrock_agents_llmobs):
    """Snapshot of the full trace including step + inner APM spans (only created when LLMObs is enabled)."""
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


@pytest.mark.skipif(BOTO_VERSION < (1, 38, 0), reason="LLMObs bedrock agent step spans require boto3 > 1.36.0")
@pytest.mark.snapshot(
    token="tests.contrib.botocore.test_bedrock_agents.test_agent_invoke_with_step_spans",
    ignores=[
        "meta.llmobs_trace_id",
        "meta.llmobs_parent_id",
        "meta._dd.p.tid",
        "meta._dd.p.llmobs_ml_app",
        "meta._dd.p.llmobs_parent_id",
        "meta._dd.p.llmobs_sd",
        "meta._dd.p.llmobs_sr",
        "meta_struct",
    ],
)
def test_agent_invoke_stream_with_step_spans(bedrock_agent_client, request_vcr, bedrock_agents_llmobs):
    """Same trace as ``test_agent_invoke_with_step_spans`` but exercises the streaming code path."""
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


def test_span_finishes_after_generator_exit(bedrock_agent_client, request_vcr, test_spans):
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
        span = test_spans.pop_traces()[0][0]
        assert span is not None
        assert span.name == "Bedrock Agent {}".format(AGENT_ID)
        assert span.resource == "aws.bedrock-agent-runtime"


def test_agent_invoke_trace_disabled(bedrock_agent_client, request_vcr, test_spans):
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
    trace = test_spans.pop_traces()[0]
    assert len(trace) == 1
    span = trace[0]
    assert span.name == "Bedrock Agent {}".format(AGENT_ID)
    assert span.resource == "aws.bedrock-agent-runtime"


@pytest.mark.skipif(BOTO_VERSION < (1, 36, 0), reason="Streaming configurations not supported in this boto3 version")
def test_agent_invoke_stream_trace_disabled(bedrock_agent_client, request_vcr, test_spans):
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
    trace = test_spans.pop_traces()[0]
    assert len(trace) == 1
    span = trace[0]
    assert span.name == "Bedrock Agent {}".format(AGENT_ID)
    assert span.resource == "aws.bedrock-agent-runtime"
