from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

import pytest

from ddtrace.ext import SpanTypes
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
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


def _llmobs_events_from_test_spans(test_spans):
    events = []
    for span in test_spans.get_spans():
        data = _get_llmobs_data_metastruct(span)
        if not data:
            continue
        tags_dict = data.get("tags") or {}
        event = {
            "span_id": str(span.span_id),
            "parent_id": data.get("parent_id") or ROOT_PARENT_ID,
            "name": data.get("name"),
            "start_ns": span.start_ns,
            "meta": data.get("meta", {}),
            "metrics": data.get("metrics", {}),
            "tags": sorted("{}:{}".format(k, v) for k, v in tags_dict.items()),
            "_dd": {"apm_trace_id": format_trace_id(span.trace_id)},
        }
        events.append(event)
    return events


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
    assert agent_span["meta"]["span"]["kind"] == "agent"
    assert "session_id:{}".format(SESSION_ID) in agent_span["tags"]


def _assert_trace_step_spans(trace_step_spans):
    assert len(trace_step_spans) == 6
    assert trace_step_spans[0]["name"].startswith("guardrailTrace Step")
    assert trace_step_spans[1]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[2]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[3]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[4]["name"].startswith("orchestrationTrace Step")
    assert trace_step_spans[5]["name"].startswith("guardrailTrace Step")
    assert all(span["meta"]["span"]["kind"] == "workflow" for span in trace_step_spans)
    assert all(span["meta"]["metadata"].get("bedrock_trace_id") for span in trace_step_spans)


def _assert_inner_span(span):
    assert span["name"] in ["guardrail", "modelInvocation", "reasoning", "location_suggestion"]
    if span["name"] == "guardrail" or span["name"] == "reasoning":
        assert span["meta"]["span"]["kind"] == "task"
        assert span["meta"]["output"].get("value") is not None
    elif span["name"] == "modelInvocation":
        assert span["meta"]["span"]["kind"] == "llm"
        assert span["meta"]["metadata"]["model_name"] == MODEL_NAME
        assert span["meta"]["metadata"]["model_provider"] == MODEL_PROVIDER
        assert span["metrics"].get("input_tokens") is not None
        assert span["metrics"].get("output_tokens") is not None
    elif span["name"] == "location_suggestion":
        assert span["meta"]["span"]["kind"] == "tool"
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


def test_agent_invoke(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans):
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
    events = sorted(_llmobs_events_from_test_spans(test_spans), key=lambda span: span["start_ns"])
    assert len(events) == 20
    # Since non-agent spans are generated by converting saved bedrock traces,
    # this means the agent span will have a way later start time than the other spans.
    _assert_agent_span(events[-1], EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_stream(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans):
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
    events = sorted(_llmobs_events_from_test_spans(test_spans), key=lambda span: span["start_ns"])
    assert len(events) == 20
    # Since non-agent spans are generated by converting saved bedrock traces,
    # this means the agent span will have a way later start time than the other spans.
    _assert_agent_span(events[-1], EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_trace_disabled(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans):
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
    events = _llmobs_events_from_test_spans(test_spans)
    assert len(events) == 1
    assert events[0]["name"] == "Bedrock Agent {}".format(AGENT_ID)


def test_agent_invoke_stream_trace_disabled(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans):
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
    events = _llmobs_events_from_test_spans(test_spans)
    assert len(events) == 1
    assert events[0]["name"] == "Bedrock Agent {}".format(AGENT_ID)


def test_translated_step_events_share_apm_trace_id_with_root(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans
):
    """Every translated step event's ``_dd.apm_trace_id`` must match the root agent span's,
    proving they were created via ``tracer.start_span(child_of=...)`` rather than synthesized.
    """
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
    events = _llmobs_events_from_test_spans(test_spans)
    assert len(events) == 20
    apm_trace_ids = {event["_dd"]["apm_trace_id"] for event in events}
    assert len(apm_trace_ids) == 1, "expected all step events to share the root agent's apm_trace_id"
    root_event = next(e for e in events if e["name"] == "Bedrock Agent {}".format(AGENT_ID))
    trace_step_spans = _extract_trace_step_spans(events)
    step_event_ids = {e["span_id"] for e in trace_step_spans}
    inner_events = _extract_inner_spans(events)
    assert all(e["parent_id"] == root_event["span_id"] for e in trace_step_spans)
    assert all(e["parent_id"] in step_event_ids for e in inner_events)


def _build_model_invocation_input_trace(trace_step_id, when):
    """Construct a minimal orchestrationTrace.modelInvocationInput event."""
    return {
        "trace": {
            "orchestrationTrace": {
                "modelInvocationInput": {
                    "traceId": trace_step_id,
                    "foundationModel": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                    "text": '{"system":"sys","messages":[{"role":"user","content":"hi"}]}',
                }
            }
        },
        "eventTime": when,
    }


def test_translate_bedrock_traces_finishes_orphaned_step_spans(bedrock_agents_llmobs, test_spans, tracer):
    """A pending ``modelInvocationInput`` without a matching output event must still be finished
    by the safety net in ``translate_bedrock_traces``.
    """
    from ddtrace.llmobs._integrations.bedrock import BedrockIntegration

    integration = BedrockIntegration(MagicMock())
    assert integration.llmobs_enabled is True

    with tracer.trace("Bedrock Agent {}".format(AGENT_ID), span_type=SpanTypes.LLM) as root_span:
        traces = [_build_model_invocation_input_trace("step-orphan", datetime.now(tz=timezone.utc))]
        integration.translate_bedrock_traces(traces, root_span)

    events = _llmobs_events_from_test_spans(test_spans)
    names = sorted(e["name"] for e in events)
    assert names == sorted(["modelInvocation", "orchestrationTrace Step"])
