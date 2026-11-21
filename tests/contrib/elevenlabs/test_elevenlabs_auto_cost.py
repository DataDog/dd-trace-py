import copy
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace import config
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._integrations import elevenlabs_cost as billing
from ddtrace.llmobs._integrations.elevenlabs import ElevenLabsIntegration
from ddtrace.llmobs._integrations.elevenlabs import create_conversation_state
from ddtrace.llmobs._integrations.elevenlabs_agents import ConversationState
from ddtrace.llmobs._routing import RoutingTarget
from tests.utils import override_global_config


def completed():
    return {
        "conversation_id": "conv-test",
        "status": "done",
        "metadata": {
            "start_time_unix_secs": 1000,
            "call_duration_secs": 10,
            "cost_fiat": 0.12,
            "charging": {"llm_price": 0.02, "platform_price": 0.1},
        },
    }


@pytest.fixture
def collector(monkeypatch, elevenlabs_llmobs):
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", True)
    jobs = []
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    def thread(target, *, name):
        jobs.append(target)

    monkeypatch.setattr(billing, "time", SimpleNamespace(sleep=sleep, monotonic=lambda: now[0]))
    monkeypatch.setattr(billing, "start_daemon_thread", thread)
    billing._active.clear()
    get = mock.Mock(return_value=completed())
    client = SimpleNamespace(conversational_ai=SimpleNamespace(conversations=SimpleNamespace(get=get)))
    writer = elevenlabs_llmobs._instance._llmobs_span_writer
    writer.enqueue.return_value = True
    yield SimpleNamespace(client=client, get=get, jobs=jobs, writer=writer, now=now)
    billing._active.clear()


def start(collector, conversation_id="conv-test", routing=None):
    billing.collect_conversation_cost(collector.client, conversation_id, routing, "elevenlabs-tests")


def test_auto_cost_preserves_usd_components_routing_and_origin_app(collector):
    start(collector, routing={"targets": [{"api_key": "tenant", "site": "datadoghq.eu"}]})
    collector.get.assert_not_called()
    collector.jobs.pop()()
    collector.get.assert_called_once_with("conv-test", request_options={"max_retries": 0, "timeout_in_seconds": 3})
    collector.writer.enqueue.assert_called_once()
    event = collector.writer.enqueue.call_args.args[0]
    assert event["meta"]["metadata"]["provider_cost"]["amounts"] == {"total": 0.12, "llm": 0.02, "platform": 0.1}
    assert event["metrics"] == {}
    assert "ml_app:elevenlabs-tests" in event["tags"]
    assert collector.writer.enqueue.call_args.kwargs["targets"] == [RoutingTarget("tenant", "datadoghq.eu")]
    assert not billing._active


def test_auto_cost_delayed_partial_zero_and_duplicate(collector):
    partial = completed()
    partial["metadata"]["cost_fiat"] = 0
    del partial["metadata"]["charging"]
    full = copy.deepcopy(partial)
    full["metadata"]["charging"] = {"llm_price": 0, "platform_price": 0}
    collector.get.side_effect = [{"status": "processing"}, partial, full]
    start(collector)
    start(collector)
    assert len(collector.jobs) == 1
    collector.jobs.pop()()
    assert collector.get.call_count == 3
    assert collector.writer.enqueue.call_count == 2
    events = [call.args[0] for call in collector.writer.enqueue.call_args_list]
    assert events[0]["span_id"] == events[1]["span_id"]
    assert events[0]["meta"]["metadata"]["provider_cost"]["amounts"] == {"total": 0}


@pytest.mark.parametrize("status,retries", [(401, 1), (403, 1), (404, 1), (429, 5), (500, 5)])
def test_auto_cost_http_errors(collector, status, retries):
    error = RuntimeError("private provider message")
    error.status_code = status
    collector.get.side_effect = error
    start(collector)
    collector.jobs.pop()()
    assert collector.get.call_count == retries
    assert collector.now[0] <= 30
    collector.writer.enqueue.assert_not_called()
    assert not billing._active


@pytest.mark.parametrize("amount", [-1, float("inf"), float("nan")])
def test_auto_cost_invalid_amount(collector, amount):
    response = completed()
    response["metadata"]["cost_fiat"] = amount
    collector.get.return_value = response
    start(collector)
    collector.jobs.pop()()
    collector.writer.enqueue.assert_not_called()


def test_auto_cost_disabled_and_no_provider_id(collector, monkeypatch):
    for value in [None, ""]:
        start(collector, value)
    assert collector.jobs == []
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", False)
    start(collector)
    assert collector.jobs == []
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", True)
    start(collector)
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", False)
    collector.jobs.pop()()
    collector.get.assert_not_called()


def test_auto_cost_disabled_llmobs(collector):
    LLMObs.disable()
    start(collector)
    assert collector.jobs == []


def test_auto_cost_disabled_while_request_pending(collector, monkeypatch):
    def get(*args, **kwargs):
        monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", False)
        return completed()

    collector.get.side_effect = get
    start(collector)
    collector.jobs.pop()()
    collector.writer.enqueue.assert_not_called()


def test_auto_cost_bounds_missing_billing_and_long_requests(collector):
    def get(*args, **kwargs):
        collector.now[0] += 3
        return {"status": "processing"}

    collector.get.side_effect = get
    start(collector)
    collector.jobs.pop()()
    assert collector.get.call_count <= 5
    assert collector.now[0] <= 30
    assert all(0 < call.kwargs["request_options"]["timeout_in_seconds"] <= 3 for call in collector.get.call_args_list)
    collector.writer.enqueue.assert_not_called()


def test_auto_cost_concurrency_and_mismatched_conversation(collector):
    for index in range(20):
        start(collector, "conv-" + str(index))
    assert len(collector.jobs) == 8
    for job in collector.jobs:
        job()
    assert collector.get.call_count == 8
    collector.writer.enqueue.assert_not_called()
    assert not billing._active


def test_auto_cost_conversation_close_once_and_releases_client(collector):
    state = ConversationState(
        ElevenLabsIntegration(config.elevenlabs),
        SimpleNamespace(client=collector.client, agent_id="agent"),
        billing_ml_app="elevenlabs-tests",
    )
    state.on_receive(
        json.dumps(
            {
                "type": "conversation_initiation_metadata",
                "conversation_initiation_metadata_event": {"conversation_id": "conv-test"},
            }
        )
    )
    state.close()
    state.close()
    assert state.billing_client is None
    assert len(collector.jobs) == 1
    collector.jobs.pop()()
    collector.writer.enqueue.assert_called_once()


def test_auto_cost_thread_start_failure_isolated(collector, monkeypatch):
    monkeypatch.setattr(billing, "start_daemon_thread", mock.Mock(side_effect=RuntimeError("no threads")))
    start(collector)
    assert not billing._active
    collector.get.assert_not_called()


def test_auto_cost_worker_does_not_delay_process_exit():
    import os
    import subprocess
    import sys
    import time

    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ddtrace.internal.threads import start_daemon_thread; "
            "import time; start_daemon_thread(lambda: time.sleep(60), name='billing-exit-test'); print('done')",
        ],
        env=dict(os.environ, DD_INSTRUMENTATION_TELEMETRY_ENABLED="false"),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert b"done" in result.stdout
    assert time.monotonic() - started < 10


def test_auto_cost_opt_out_is_latched_at_session_start(collector, monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", False)
    state = create_conversation_state(SimpleNamespace(client=collector.client, agent_id="agent"), None)
    monkeypatch.setitem(config.elevenlabs, "conversation_cost_enabled", True)
    state.on_receive(
        json.dumps(
            {
                "type": "conversation_initiation_metadata",
                "conversation_initiation_metadata_event": {"conversation_id": "conv-test"},
            }
        )
    )
    state.close()
    assert collector.jobs == []


def test_auto_cost_application_is_captured_at_session_start(collector):
    state = create_conversation_state(SimpleNamespace(client=collector.client, agent_id="agent"), None)
    state.on_receive(
        json.dumps(
            {
                "type": "conversation_initiation_metadata",
                "conversation_initiation_metadata_event": {"conversation_id": "conv-test"},
            }
        )
    )
    with override_global_config({"_llmobs_ml_app": "another-app"}):
        state.close()
        collector.jobs.pop()()
    assert "ml_app:elevenlabs-tests" in collector.writer.enqueue.call_args.args[0]["tags"]


@pytest.mark.parametrize("replace_during_request", [False, True])
@pytest.mark.parametrize("replace_writer_only", [False, True])
def test_auto_cost_does_not_cross_service_generations(
    collector, monkeypatch, replace_during_request, replace_writer_only
):
    other_writer = mock.Mock()
    replacement = SimpleNamespace(_llmobs_span_writer=other_writer)
    with monkeypatch.context() as context:

        def replace():
            if replace_writer_only:
                context.setattr(LLMObs._instance, "_llmobs_span_writer", other_writer)
            else:
                context.setattr(LLMObs, "_instance", replacement)

        start(collector)
        if replace_during_request:

            def get(*args, **kwargs):
                replace()
                return completed()

            collector.get.side_effect = get
        else:
            replace()
        collector.jobs.pop()()
    collector.writer.enqueue.assert_not_called()
    other_writer.enqueue.assert_not_called()
    assert collector.get.call_count == int(replace_during_request)


@pytest.mark.parametrize("distributed", [False, True])
def test_auto_cost_inherits_parent_application(collector, distributed):
    from ddtrace.internal.native._native import Context
    from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY

    with LLMObs.workflow(agent_service="voice-app") as workflow:
        parent = Context(meta={PROPAGATED_ML_APP_KEY: "voice-app"}) if distributed else workflow
        state = create_conversation_state(SimpleNamespace(client=collector.client, agent_id="agent"), parent)
    assert state.billing_ml_app == "voice-app"
    state.provider_conversation_id = "conv-test"
    state.close()
    collector.jobs.pop()()
    assert "ml_app:voice-app" in collector.writer.enqueue.call_args.args[0]["tags"]


def test_auto_cost_discards_a_response_after_its_deadline(collector):
    def get(*args, **kwargs):
        collector.now[0] += 31
        return completed()

    collector.get.side_effect = get
    start(collector)
    collector.jobs.pop()()
    collector.get.assert_called_once()
    collector.writer.enqueue.assert_not_called()
    assert not billing._active
