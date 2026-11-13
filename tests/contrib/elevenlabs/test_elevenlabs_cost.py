from concurrent.futures import ThreadPoolExecutor
import copy
import json
import math
from pathlib import Path
import threading
from unittest import mock

from elevenlabs.types import GetConversationResponseModel
import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._provider_cost import build_conversation_cost_event
from ddtrace.llmobs._routing import RoutingTarget
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.utils import override_global_config


_FIXTURES = Path(__file__).with_name("fixtures")
_CASES = json.loads((_FIXTURES / "elevenlabs-conversation-costs.json").read_text())
_GOLDEN = json.loads((_FIXTURES / "elevenlabs-conversation-cost.json").read_text())


@pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
def test_submit_conversation_cost_shared_fixtures(elevenlabs_llmobs, case):
    response = copy.deepcopy(case["response"])
    before = copy.deepcopy(response)
    submitted = LLMObs.submit_conversation_cost(response, agent_service="test", total_cost=case.get("total_cost"))
    assert submitted is case["valid"]
    writer = elevenlabs_llmobs._instance._llmobs_span_writer
    if not submitted:
        writer.enqueue.assert_not_called()
    else:
        writer.enqueue.assert_called_once()
        event = writer.enqueue.call_args.args[0]
        cost = event["meta"]["metadata"]["provider_cost"]
        assert cost["provider"] == "elevenlabs"
        assert cost["currency"] == "USD"
        assert cost["scope"] == "conversation"
        assert cost["owner"] == _GOLDEN["owner"]
        assert event["session_id"] == response["conversation_id"]
        assert event["metrics"] == ({} if "total_cost" not in case else {"total_cost": case["total_cost"]})
        assert event["meta"]["span"]["kind"] == "workflow"
        assert event["meta"]["metadata"]["ttfa_eligible"] is False
        assert "input" not in event["meta"]
        assert "output" not in event["meta"]
        assert "model_provider" not in event["meta"]
    assert response == before


def test_sdk_model_and_golden_contract(elevenlabs_llmobs):
    response = GetConversationResponseModel(**_CASES[0]["response"])
    before = repr(response)
    assert LLMObs.submit_conversation_cost(response, agent_service="test")
    event = elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.call_args.args[0]
    assert event["meta"]["metadata"]["provider_cost"] == _GOLDEN
    assert repr(response) == before


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, -1, True])
def test_invalid_billing_and_override_are_rejected(elevenlabs_llmobs, invalid):
    response = copy.deepcopy(_CASES[0]["response"])
    response["metadata"]["cost_fiat"] = invalid
    assert LLMObs.submit_conversation_cost(response, agent_service="test") is False
    response["metadata"]["cost_fiat"] = 0.12
    assert LLMObs.submit_conversation_cost(response, agent_service="test", total_cost=invalid) is False
    elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.assert_not_called()


def test_zero_override_is_in_dollars(elevenlabs_llmobs):
    assert LLMObs.submit_conversation_cost(_CASES[0]["response"], agent_service="test", total_cost=0)
    event = elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.call_args.args[0]
    assert event["metrics"] == {"total_cost": 0}
    assert event["meta"]["metadata"]["provider_cost"]["amounts"]["total"] == 0.12


def test_delayed_and_duplicate_sources_have_one_owner(elevenlabs_llmobs):
    partial = next(case["response"] for case in _CASES if case["name"] == "partial_llm")
    complete = _CASES[0]["response"]
    assert LLMObs.submit_conversation_cost(partial, agent_service="test")
    assert LLMObs.submit_conversation_cost(complete, agent_service="test")
    assert LLMObs.submit_conversation_cost(complete, agent_service="test", source="post_call_transcription")
    events = [call.args[0] for call in elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.call_args_list]
    assert len(events) == 3
    assert len({(event["trace_id"], event["span_id"]) for event in events}) == 1
    assert "total" not in events[0]["meta"]["metadata"]["provider_cost"]["amounts"]
    assert events[-1]["meta"]["metadata"]["provider_cost"]["provenance"]["total"]["kind"] == "post_call_transcription"


def test_disabled_billing_does_not_start_a_service(elevenlabs_llmobs):
    writer = elevenlabs_llmobs._instance._llmobs_span_writer
    LLMObs.disable()
    assert LLMObs.submit_conversation_cost(_CASES[0]["response"], agent_service="test") is False
    writer.enqueue.assert_not_called()


def test_unicode_application_owner_is_stable():
    first = build_conversation_cost_event(_CASES[0]["response"], "voice<&é🦊")
    second = build_conversation_cost_event(_CASES[0]["response"], "voice<&é🦊")
    assert first is not None and second is not None
    assert (first["trace_id"], first["span_id"]) == (second["trace_id"], second["span_id"])


def test_billing_uses_the_callers_tenant_routing(elevenlabs_llmobs):
    with LLMObs.routing_context(dd_api_key="test-routed-key", dd_site="datad0g.com"):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"], agent_service="test")
    call = elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.call_args
    assert call.kwargs["targets"] == [RoutingTarget(api_key="test-routed-key", site="datad0g.com")]


@pytest.fixture(autouse=True)
def accepting_billing_writer(elevenlabs_llmobs):
    elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.return_value = True


@pytest.fixture
def actual_billing_writer(elevenlabs_llmobs, monkeypatch):
    writer = LLMObsSpanWriter(1, 1, is_agentless=True, _api_key="test-key")
    flush = mock.Mock()
    monkeypatch.setattr(writer, "BUFFER_LIMIT", 1)
    monkeypatch.setattr(writer, "_flush_batch", flush)
    monkeypatch.setattr(elevenlabs_llmobs._instance, "_llmobs_span_writer", writer)
    return writer, flush


@pytest.mark.parametrize("service, expected", [("voice-app", "voice-app"), (None, "unnamed-ml-app")])
def test_billing_resolves_service_and_default_application(elevenlabs_llmobs, service, expected):
    with override_global_config({"_llmobs_ml_app": None, "service": service}):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True
    event = elevenlabs_llmobs._instance._llmobs_span_writer.enqueue.call_args.args[0]
    assert "ml_app:" + expected in event["tags"]


def test_billing_reports_full_default_queue(actual_billing_writer):
    writer, flush = actual_billing_writer
    assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True
    assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is False
    writer.periodic()
    flush.assert_called_once()
    assert len(flush.call_args.args[0]) == 1
    assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True


def test_billing_reports_full_routed_queue(actual_billing_writer):
    writer, flush = actual_billing_writer
    with LLMObs.routing_context(dd_api_key="routed-key", dd_site="datad0g.com"):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is False
    writer.periodic()
    flush.assert_called_once()
    assert len(flush.call_args.args[0]) == 1
    assert flush.call_args.args[1] == RoutingTarget(api_key="routed-key", site="datad0g.com")


@pytest.mark.parametrize("full_target", [0, 1])
def test_billing_reports_partial_target_queueing(actual_billing_writer, full_target):
    writer, flush = actual_billing_writer
    targets = [
        {"dd_api_key": "first-routed-key", "dd_site": "datad0g.com"},
        {"dd_api_key": "second-routed-key", "dd_site": "datad0g.com"},
    ]
    with LLMObs.routing_context(**targets[full_target]):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True
    with LLMObs.routing_context(targets=targets):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is False
    writer.periodic()
    assert flush.call_count == 2
    assert {call.args[1].api_key for call in flush.call_args_list} == {target["dd_api_key"] for target in targets}
    assert all(len(call.args[0]) == 1 for call in flush.call_args_list)


def test_billing_reports_all_target_queues_full(actual_billing_writer):
    writer, flush = actual_billing_writer
    with LLMObs.routing_context(targets=[{"dd_api_key": "first-key"}, {"dd_api_key": "second-key"}]):
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is True
        assert LLMObs.submit_conversation_cost(_CASES[0]["response"]) is False
    writer.periodic()
    assert flush.call_count == 2
    assert all(len(call.args[0]) == 1 for call in flush.call_args_list)


def test_billing_queue_acknowledgement_is_atomic(actual_billing_writer):
    writer, flush = actual_billing_writer
    barrier = threading.Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return LLMObs.submit_conversation_cost(_CASES[0]["response"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(submit), pool.submit(submit)]]
    assert sorted(results) == [False, True]
    writer.periodic()
    flush.assert_called_once()
    assert len(flush.call_args.args[0]) == 1
