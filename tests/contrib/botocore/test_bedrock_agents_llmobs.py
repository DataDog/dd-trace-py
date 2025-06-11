import pytest

from tests.contrib.botocore.bedrock_utils import AGENT_ALIAS_ID
from tests.contrib.botocore.bedrock_utils import AGENT_ID
from tests.contrib.botocore.bedrock_utils import AGENT_INPUT
from tests.contrib.botocore.bedrock_utils import BOTO_VERSION


pytestmark = pytest.mark.skipif(
    BOTO_VERSION < (1, 38, 0), reason="LLMObs bedrock agent traces are only supported for boto3 > 1.36.0"
)

EXPECTED_OUTPUT = (
    "Based on your preferences for a beach vacation with nature and outdoor adventures, I recommend a "
    "7-day trip to Manuel Antonio, Costa Rica. This destination offers beautiful beaches, lush nature, "
    "and plenty of outdoor activities.\n\nThe best time to visit Manuel Antonio is during the dry "
    "season, from December to April. This period offers ideal weather for beach activities and outdoor "
    "adventures. The average cost for a luxury trip to Manuel Antonio is around $200-$300 per day, "
    "which aligns well with your preference for 4/5 star resorts.\n\nIn Manuel Antonio, "
    "you can enjoy:\n1. Lounging on pristine beaches like Playa Manuel Antonio and Playa Espadilla\n2. "
    "Exploring Manuel Antonio National Park, known for its diverse wildlife and hiking trails\n3. "
    "Luxury resorts offering all-inclusive packages with stunning ocean views\n4. Adventure activities "
    "such as zip-lining, white-water rafting, and snorkeling\n\nThis destination perfectly combines "
    "your desire for beach relaxation, nature experiences, and outdoor adventures, all while providing "
    "the luxury accommodations you prefer."
)
SESSION_ID = "test_session"
MODEL_NAME = "claude-3-5-sonnet-20240620-v1:0"
MODEL_PROVIDER = "anthropic"


def _extract_trace_step_spans(events):
    trace_step_spans = []
    for span in events:
        if span["name"].endswith("Step"):
            trace_step_spans.append(span)
    return trace_step_spans


def _extract_inner_spans(events):
    inner_spans = []
    for span in events:
        if span["name"].endswith("Step") or span["name"].startswith("Bedrock Agent"):
            continue
        inner_spans.append(span)
    return inner_spans


def _assert_agent_span(agent_span, resp_str):
    assert agent_span["name"] == "Bedrock Agent {}".format(AGENT_ID)
    assert agent_span["meta"]["input"]["value"] == AGENT_INPUT
    assert agent_span["meta"]["output"]["value"] == resp_str
    assert agent_span["meta"]["metadata"]["agent_alias_id"] == AGENT_ALIAS_ID
    assert agent_span["meta"]["metadata"]["agent_id"] == AGENT_ID
    assert agent_span["meta"]["span.kind"] == "agent"
    assert "session_id:{}".format(SESSION_ID) in agent_span["tags"]


def _assert_trace_step_spans(trace_step_spans):
    assert len(trace_step_spans) == 6
    assert trace_step_spans[0]["name"].startswith("guardrailTrace Step")
    assert trace_step_spans[1]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[2]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[3]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[4]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[5]["name"].startswith("guardrailTrace Step")
    assert all(span["meta"]["span.kind"] == "workflow" for span in trace_step_spans)
    assert all(span["meta"]["metadata"]["bedrock_trace_id"] == span["span_id"] for span in trace_step_spans)


def _assert_inner_span(span):
    assert span["name"] in ["guardrail", "modelInvocation", "reasoning", "location_suggestion"]
    if span["name"] == "guardrail":
        assert span["meta"]["span.kind"] == "task"
        assert span["meta"]["output"].get("value") is not None
    elif span["name"] == "modelInvocation":
        assert span["meta"]["span.kind"] == "llm"
        assert span["meta"]["metadata"]["model_name"] == MODEL_NAME
        assert span["meta"]["metadata"]["model_provider"] == MODEL_PROVIDER
        assert span["metrics"].get("input_tokens") is not None
        assert span["metrics"].get("output_tokens") is not None
    elif span["name"] == "reasoning":
        assert span["meta"]["span.kind"] == "task"
        assert span["meta"]["output"].get("value") is not None
    elif span["name"] == "location_suggestion":
        assert span["meta"]["span.kind"] == "tool"
        assert span["meta"]["output"].get("value") is not None


def _assert_inner_spans(inner_spans, trace_step_spans):
    expected_inner_spans_per_step = [1, 3, 3, 3, 2, 1]
    assert len(inner_spans) == 13
    inner_spans_by_trace_step = {
        trace_step_span["span_id"]: [span for span in inner_spans if span["parent_id"] == trace_step_span["span_id"]]
        for trace_step_span in trace_step_spans
    }
    for i, trace_step_span in enumerate(trace_step_spans):
        for inner_span in inner_spans_by_trace_step[trace_step_span["span_id"]]:
            _assert_inner_span(inner_span)
        assert len(inner_spans_by_trace_step[trace_step_span["span_id"]]) == expected_inner_spans_per_step[i]


def test_agent_invoke(bedrock_agent_client, request_vcr, llmobs_events):
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
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 20
    # Since non-agent spans are generated by converting saved bedrock traces,
    # this means the agent span will have a way later start time than the other spans.
    _assert_agent_span(llmobs_events[-1], EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(llmobs_events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(llmobs_events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_stream(bedrock_agent_client, request_vcr, llmobs_events):
    with request_vcr.use_cassette("agent_invoke.yaml"):
        response = bedrock_agent_client.invoke_agent(
            agentAliasId=AGENT_ALIAS_ID,
            agentId=AGENT_ID,
            sessionId="test_session",
            enableTrace=True,
            inputText=AGENT_INPUT,
            streamingConfigurations={"applyGuardrailInterval": 10000, "streamFinalResponse": True},
        )
        for _ in response["completion"]:
            pass
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(llmobs_events) == 20
    # Since non-agent spans are generated by converting saved bedrock traces,
    # this means the agent span will have a way later start time than the other spans.
    _assert_agent_span(llmobs_events[-1], EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(llmobs_events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(llmobs_events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_trace_disabled(bedrock_agent_client, request_vcr, llmobs_events):
    """Test that we only get the agent span when enableTrace is set to False."""
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
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["name"] == "Bedrock Agent {}".format(AGENT_ID)


def test_agent_invoke_stream_trace_disabled(bedrock_agent_client, request_vcr, llmobs_events):
    """Test that we only get the agent span when enableTrace is set to False."""
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
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["name"] == "Bedrock Agent {}".format(AGENT_ID)
