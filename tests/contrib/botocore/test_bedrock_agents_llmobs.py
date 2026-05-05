from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

import pytest

from ddtrace.ext import SpanTypes
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
    # bedrock_trace_id is now retained as metadata only (the AWS trace step id is no longer used as
    # the LLMObs span_id; that comes from the underlying Datadog APM span).
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


def test_agent_invoke(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans, llmobs_events):
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
    # The agent span is stretched to cover its back-dated children (so it no longer has the latest
    # start_ns); look it up by name instead of relying on sort order.
    agent_span = next(e for e in llmobs_events if e["name"].startswith("Bedrock Agent"))
    _assert_agent_span(agent_span, EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(llmobs_events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(llmobs_events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_stream(bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans, llmobs_events):
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
    # The agent span is stretched to cover its back-dated children (so it no longer has the latest
    # start_ns); look it up by name instead of relying on sort order.
    agent_span = next(e for e in llmobs_events if e["name"].startswith("Bedrock Agent"))
    _assert_agent_span(agent_span, EXPECTED_OUTPUT)
    trace_step_spans = _extract_trace_step_spans(llmobs_events)
    _assert_trace_step_spans(trace_step_spans)
    inner_spans = _extract_inner_spans(llmobs_events)
    _assert_inner_spans(inner_spans, trace_step_spans)


def test_agent_invoke_trace_disabled(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans, llmobs_events
):
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


def test_agent_invoke_stream_trace_disabled(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans, llmobs_events
):
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


def test_translate_bedrock_traces_does_not_share_state_across_invocations(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, llmobs_events
):
    """Two ``invoke_agent`` calls in the same process must each produce the same number of LLMObs
    events. With the previous class-level state dicts the second invocation could leak entries
    from the first (or skip entries that were marked as already-active). Regression test for the
    per-invocation state migration.
    """
    counts = []
    for _ in range(2):
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
        counts.append(len(llmobs_events))
    assert counts[0] > 0, "expected the cassette to produce LLMObs events"
    assert counts[1] == 2 * counts[0], "second invocation produced a different event count than the first"


def test_agent_span_covers_back_dated_children(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, test_spans, llmobs_events
):
    """The root agent span's start/end must encompass every back-dated child span. The cassette's
    AWS event times are far in the past, while the root span starts at test runtime; without the
    stretch in ``translate_bedrock_traces`` + ``TracedBotocoreEventStream``, children would
    appear to start before and end after their parent.
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
    assert len(llmobs_events) == 20
    root_event = next(e for e in llmobs_events if e["name"].startswith("Bedrock Agent"))
    children = [e for e in llmobs_events if e is not root_event]
    root_start = root_event["start_ns"]
    root_end = root_start + root_event["duration"]
    for child in children:
        child_start = child["start_ns"]
        child_end = child_start + child["duration"]
        assert child_start >= root_start, f"{child['name']} starts before root"
        assert child_end <= root_end, f"{child['name']} ends after root"


def test_translated_step_events_share_apm_trace_id_with_root(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, llmobs_events
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
    assert len(llmobs_events) == 20
    apm_trace_ids = {event["_dd"]["apm_trace_id"] for event in llmobs_events}
    assert len(apm_trace_ids) == 1, "expected all step events to share the root agent's apm_trace_id"
    root_event = next(e for e in llmobs_events if e["name"] == "Bedrock Agent {}".format(AGENT_ID))
    trace_step_spans = _extract_trace_step_spans(llmobs_events)
    step_event_ids = {e["span_id"] for e in trace_step_spans}
    inner_events = _extract_inner_spans(llmobs_events)
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


def test_translate_bedrock_traces_finishes_orphaned_step_spans(bedrock_agents_llmobs, llmobs_events, tracer):
    """A pending ``modelInvocationInput`` without a matching output event must still be finished
    by the safety net in ``translate_bedrock_traces``.
    """
    from ddtrace.llmobs._integrations.bedrock import BedrockIntegration

    integration = BedrockIntegration(MagicMock())
    assert integration.llmobs_enabled is True

    with tracer.trace("Bedrock Agent {}".format(AGENT_ID), span_type=SpanTypes.LLM) as root_span:
        traces = [_build_model_invocation_input_trace("step-orphan", datetime.now(tz=timezone.utc))]
        integration.translate_bedrock_traces(traces, root_span)

    names = sorted(e["name"] for e in llmobs_events)
    assert names == sorted(["modelInvocation", "orchestrationTrace Step"])


def test_step_spans_carry_inner_io_in_production(
    bedrock_agent_client, request_vcr, bedrock_agents_llmobs, llmobs_events
):
    """Regression test: step spans should propagate first input and last output from their inner spans."""
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
    orchestration_steps = [e for e in llmobs_events if e["name"].startswith("orchestrationTrace Step")]
    assert orchestration_steps, "expected at least one orchestrationTrace Step event"
    for step in orchestration_steps:
        meta = step.get("meta") or {}
        # workflow step spans should carry the propagated input messages from the first inner
        # modelInvocation child, and an output value from the last inner observation/output.
        assert meta.get("input"), f"orchestrationTrace Step {step.get('span_id')} missing input"
        assert meta.get("output"), f"orchestrationTrace Step {step.get('span_id')} missing output"
