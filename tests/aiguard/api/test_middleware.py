from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard import Message
from ddtrace.aiguard import Options
from ddtrace.aiguard import middleware
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.middleware import AIGuardMiddleware
from ddtrace.aiguard.middleware import EvaluationDirection
from ddtrace.aiguard.middleware import EvaluationTarget
from ddtrace.aiguard.middleware import register_middleware
from ddtrace.internal.native import ConnectionFailedError
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_response


MESSAGES = [Message(role="user", content="hello")]
TOOL_CALL = [
    Message(role="user", content="weather?"),
    Message(
        role="assistant",
        tool_calls=[{"id": "call_1", "function": {"name": "get_weather", "arguments": "{}"}}],
    ),
]


@pytest.fixture(autouse=True)
def clear_middlewares():
    middleware._MIDDLEWARES.clear()
    yield
    middleware._MIDDLEWARES.clear()


@pytest.fixture
def execute_request():
    with patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request") as mocked:
        mocked.return_value = mock_evaluate_response("ALLOW")
        yield mocked


@pytest.fixture
def telemetry_mock():
    with patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric") as mocked:
        yield mocked


def _request_metric_tags(telemetry_mock):
    return [args[3] for args, _ in telemetry_mock.call_args_list if args[1] == AI_GUARD.REQUESTS_METRIC]


class Recorder(AIGuardMiddleware):
    def __init__(self, decision=None, calls=None, name="recorder"):
        self.decision = decision
        self.calls = calls if calls is not None else []
        self.name = name
        self.contexts = []

    def should_evaluate(self, context):
        self.calls.append((self.name, "should_evaluate"))
        return self.decision

    def pre_evaluate(self, context):
        self.calls.append((self.name, "pre_evaluate"))
        self.contexts.append(context)

    def post_evaluate(self, context):
        self.calls.append((self.name, "post_evaluate"))
        self.contexts.append(context)


def test_registration_unregister_is_idempotent(ai_guard_client, execute_request):
    recorder = Recorder()
    registration = register_middleware(recorder)
    other = register_middleware(recorder)

    registration.unregister()
    registration.unregister()

    assert middleware._MIDDLEWARES == [recorder]
    other.unregister()
    ai_guard_client.evaluate(MESSAGES)
    assert recorder.calls == []


def test_should_evaluate_false_skips(ai_guard_client, test_spans, execute_request, telemetry_mock):
    recorder = Recorder(decision=False)
    register_middleware(recorder)

    result = ai_guard_client.evaluate(MESSAGES)

    assert result["action"] == "ALLOW"
    assert result["reason"] == "skipped by middleware"
    assert result["messages"] is MESSAGES
    assert test_spans.spans == []
    execute_request.assert_not_called()
    assert recorder.calls == [("recorder", "should_evaluate")]
    assert _request_metric_tags(telemetry_mock) == [
        (("skipped", "true"), ("source", AI_GUARD.SOURCE_SDK), ("integration", AI_GUARD.INTEGRATION_NONE))
    ]


@pytest.mark.parametrize("decision", [None, True])
def test_none_or_true_do_not_skip(ai_guard_client, test_spans, execute_request, decision):
    register_middleware(Recorder(decision=decision))

    ai_guard_client.evaluate(MESSAGES)

    execute_request.assert_called_once()
    find_ai_guard_span(test_spans)


def test_skip_short_circuits_later_middlewares(ai_guard_client, execute_request):
    calls = []
    register_middleware(Recorder(decision=False, calls=calls, name="first"))
    register_middleware(Recorder(calls=calls, name="second"))

    ai_guard_client.evaluate(MESSAGES)

    assert calls == [("first", "should_evaluate")]


def test_should_evaluate_context(ai_guard_client, tracer, execute_request):
    seen = {}

    class Inspector(AIGuardMiddleware):
        def should_evaluate(self, context):
            seen["ai_guard_span"] = context.ai_guard_span
            seen["local_root_span"] = context.local_root_span
            seen["request"] = context.request

    register_middleware(Inspector())

    with tracer.trace("web.request") as root:
        ai_guard_client.evaluate(MESSAGES)

    assert seen["ai_guard_span"] is None
    assert seen["local_root_span"].span_id == root.span_id
    request = seen["request"]
    assert request.messages is MESSAGES
    assert (request.source, request.integration) == (AI_GUARD.SOURCE_SDK, AI_GUARD.INTEGRATION_NONE)
    assert request.direction is EvaluationDirection.INPUT
    assert request.target is EvaluationTarget.PROMPT


def test_request_direction_and_target_for_tool_call(ai_guard_client, execute_request):
    recorder = Recorder()
    register_middleware(recorder)

    ai_guard_client.evaluate(TOOL_CALL)

    request = recorder.contexts[0].request
    assert request.direction is EvaluationDirection.OUTPUT
    assert request.target is EvaluationTarget.TOOL


def test_pre_evaluate_sees_core_tagged_span(ai_guard_client, test_spans, execute_request):
    core_tags = {}

    class Tagger(AIGuardMiddleware):
        def pre_evaluate(self, context):
            span = context.ai_guard_span
            for tag in (AI_GUARD.TARGET_TAG, AI_GUARD.DIRECTION_TAG, AI_GUARD.SOURCE_TAG, AI_GUARD.INTEGRATION_TAG):
                core_tags[tag] = span.get_tag(tag)
            span.set_tag("my_app.pre", "1")

    register_middleware(Tagger())

    evaluate_auto(ai_guard_client, TOOL_CALL, AI_GUARD.INTEGRATION_OPENAI)

    assert core_tags == {
        AI_GUARD.TARGET_TAG: "tool",
        AI_GUARD.DIRECTION_TAG: "output",
        AI_GUARD.SOURCE_TAG: AI_GUARD.SOURCE_AUTO,
        AI_GUARD.INTEGRATION_TAG: AI_GUARD.INTEGRATION_OPENAI,
    }
    assert find_ai_guard_span(test_spans).get_tag("my_app.pre") == "1"


def test_evaluated_requests_report_skipped_false(ai_guard_client, execute_request, telemetry_mock):
    register_middleware(Recorder())

    ai_guard_client.evaluate(MESSAGES)

    assert [("skipped", "false") in tags for tags in _request_metric_tags(telemetry_mock)] == [True]


@pytest.mark.parametrize("action", ["ALLOW", "DENY", "ABORT"])
def test_post_evaluate_runs_once_without_blocking(ai_guard_client, execute_request, action):
    execute_request.return_value = mock_evaluate_response(action)
    recorder = Recorder()
    register_middleware(recorder)

    result = ai_guard_client.evaluate(MESSAGES, Options(block=False))

    assert recorder.calls == [
        ("recorder", "should_evaluate"),
        ("recorder", "pre_evaluate"),
        ("recorder", "post_evaluate"),
    ]
    context = recorder.contexts[-1]
    assert context.result is result
    assert context.error is None


def test_post_evaluate_runs_once_on_blocking_abort(ai_guard_client, execute_request):
    execute_request.return_value = mock_evaluate_response("DENY", reason="nope", block=True)
    recorder = Recorder()
    register_middleware(recorder)

    with pytest.raises(AIGuardAbortError) as exc_info:
        ai_guard_client.evaluate(MESSAGES, Options(block=True))

    assert [call[1] for call in recorder.calls].count("post_evaluate") == 1
    context = recorder.contexts[-1]
    assert context.result["action"] == "DENY"
    assert context.error is exc_info.value


def test_post_evaluate_runs_on_client_error(ai_guard_client, execute_request):
    execute_request.side_effect = ConnectionFailedError("refused")
    recorder = Recorder()
    register_middleware(recorder)

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(MESSAGES)

    assert [call[1] for call in recorder.calls].count("post_evaluate") == 1
    context = recorder.contexts[-1]
    assert context.result is None
    assert context.error is exc_info.value


def test_hook_exceptions_are_contained(ai_guard_client, execute_request):
    class Broken(AIGuardMiddleware):
        def should_evaluate(self, context):
            raise RuntimeError("boom")

        def pre_evaluate(self, context):
            raise RuntimeError("boom")

        def post_evaluate(self, context):
            raise RuntimeError("boom")

    register_middleware(Broken())

    result = ai_guard_client.evaluate(MESSAGES)

    assert result["action"] == "ALLOW"
    execute_request.assert_called_once()


def test_auto_instrumentation_runs_middlewares(ai_guard_client, execute_request):
    recorder = Recorder()
    register_middleware(recorder)

    evaluate_auto(ai_guard_client, MESSAGES, AI_GUARD.INTEGRATION_OPENAI)

    request = recorder.contexts[0].request
    assert request.source == AI_GUARD.SOURCE_AUTO
    assert request.integration == AI_GUARD.INTEGRATION_OPENAI
    assert [call[1] for call in recorder.calls] == ["should_evaluate", "pre_evaluate", "post_evaluate"]
