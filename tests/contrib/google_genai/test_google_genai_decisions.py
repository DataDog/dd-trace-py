import json
from unittest import mock

import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_output_messages
from tests.llmobs._utils import assert_llmobs_span_data


genai_module = pytest.importorskip("google.genai")
pytestmark = pytest.mark.skipif(
    parse_version(genai_module.__version__) < (3, 2, 0), reason="Decisions API only available google-genai >= 3.2.0"
)

DECISION_INPUT = "Customer says: I was charged twice for my order, please refund one of the charges."
DECISION_QUESTIONS = {
    "needs_refund": {"type": "noul", "instructions": "Does the customer ask for a refund?"},
    "topic": {
        "type": "choice",
        "instructions": "What is the ticket about?",
        "criteria": {"billing": "Payments and charges", "shipping": "Delivery", "other": ""},
    },
    "urgency": {"type": "score", "instructions": "How urgent is the ticket?", "criteria": ["low", "medium", "high"]},
}
DECISION_ANSWERS = {
    "needs_refund": {"type": "noul", "noul": 0.97, "confidence": 0.97},
    "topic": {
        "type": "choice",
        "choice": "billing",
        "confidence": 0.94,
        "probabilities": {"billing": 0.94, "shipping": 0.04, "other": 0.02},
    },
    "urgency": {
        "type": "score",
        "score": 1.2,
        "confidence": 0.61,
        "probabilities": {"0": 0.1, "1": 0.61, "2": 0.29},
        "legend": {"0": "low", "1": "medium", "2": "high"},
    },
}
DECISION_RESPONSE_BODY = {
    "answers": DECISION_ANSWERS,
    "usage": {"total_input_tokens": 120, "total_output_tokens": 0, "total_tokens": 120, "total_cached_tokens": 64},
}
DECISION_IMAGE_DATA = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
COMMON_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.google_genai",
    "integration": "google_genai",
    "decision_provider": "google",
}


@pytest.fixture
def decisions_http(genai):
    """Replace the HTTP call of the generated SDK and record the JSON bodies it sends."""
    from google.genai._gaos import basesdk
    from google.genai._gaos import decisions

    state = {"bodies": [], "status": 200, "body": DECISION_RESPONSE_BODY}

    def _response(request, stream):
        state["bodies"].append(json.loads(request.content))
        content = json.dumps(state["body"]).encode()
        kwargs = {"headers": {"content-type": "application/json"}, "request": request}
        if stream:
            return decisions.httpx.Response(state["status"], stream=decisions.httpx.ByteStream(content), **kwargs)
        return decisions.httpx.Response(state["status"], content=content, **kwargs)

    def _do_request(self, hook_ctx, request, is_error_status_code, stream=False, retry_config=None):
        return _response(request, stream)

    async def _do_request_async(self, hook_ctx, request, is_error_status_code, stream=False, retry_config=None):
        return _response(request, stream)

    with mock.patch.object(basesdk.BaseSDK, "do_request", _do_request):
        with mock.patch.object(basesdk.AsyncBaseSDK, "do_request_async", _do_request_async):
            yield state


def _spans(test_spans):
    return [s for trace in test_spans.pop_traces() for s in trace]


def _decision_io(span):
    input_messages = get_llmobs_input_messages(span)
    output_messages = get_llmobs_output_messages(span)
    assert len(input_messages) == 1 and input_messages[0]["role"] == "user"
    assert len(output_messages) == 1 and output_messages[0]["role"] == "assistant"
    output = output_messages[0]["content"]
    return json.loads(input_messages[0]["content"]), json.loads(output) if output else None


def _expected_span_data(**overrides):
    kwargs = {
        "span_kind": "llm",
        "model_name": "decisions-prerelease",
        "model_provider": "google",
        "input_messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {"model": "decisions-prerelease", "input": DECISION_INPUT, "questions": DECISION_QUESTIONS},
                    sort_keys=True,
                ),
            }
        ],
        "output_messages": [
            {"role": "assistant", "content": json.dumps({"answers": DECISION_ANSWERS}, sort_keys=True)}
        ],
        "metrics": {"input_tokens": 120, "output_tokens": 0, "total_tokens": 120, "cache_read_input_tokens": 64},
        "tags": COMMON_TAGS,
    }
    kwargs.update(overrides)
    return kwargs


def test_decision(genai, genai_llmobs, test_spans, decisions_http):
    client = genai.Client()
    response = client.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert response.answers["topic"].choice == "billing"
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].resource == "Decisions.create"
    assert spans[0].get_tag("google_genai.request.provider") == "google"
    assert spans[0].get_tag("google_genai.request.model") == "decisions-prerelease"
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())
    request, response_body = _decision_io(spans[0])
    assert request == decisions_http["bodies"][0]
    assert response_body == {"answers": DECISION_ANSWERS}


async def test_decision_async(genai, genai_llmobs, test_spans, decisions_http):
    client = genai.Client()
    await client.aio.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].resource == "AsyncDecisions.create"
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())


def test_decision_model_path(genai, genai_llmobs, test_spans, decisions_http):
    """The model is recorded without its models/ prefix, and the request keeps it as sent."""
    client = genai.Client()
    client.decisions.create(model="models/decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    spans = _spans(test_spans)
    assert _get_llmobs_data_metastruct(spans[0])["meta"]["model_name"] == "decisions-prerelease"
    request, _ = _decision_io(spans[0])
    assert request["model"] == "models/decisions-prerelease"


def test_decision_media_and_extra_body(genai, genai_llmobs, test_spans, decisions_http):
    """Inline media data is replaced by a marker, and only request fields are taken from extra_body."""
    from google.genai import decisions

    decision_input = [
        {"type": "text", "text": DECISION_INPUT},
        {"type": "image", "data": DECISION_IMAGE_DATA, "mime_type": "image/png"},
        {"type": "image", "uri": "gs://bucket/receipt.png", "mime_type": "image/png"},
        {"type": "audio", "data": "UklGRg==", "mime_type": "audio/wav"},
        {"type": "document", "data": "JVBERi0=", "mime_type": "application/pdf"},
    ]
    questions = {"needs_refund": decisions.NoulQuestion(instructions="Does the customer ask for a refund?")}
    client = genai.Client()
    client.decisions.create(
        model="decisions-prerelease",
        input=decision_input,
        questions=questions,
        extra_body={"model": "decisions-prerelease-2", "api_token": "secret"},
    )
    spans = _spans(test_spans)
    assert len(spans) == 1
    request, _ = _decision_io(spans[0])
    assert request == {
        "model": "decisions-prerelease-2",
        "input": [
            {"type": "text", "text": DECISION_INPUT},
            {"type": "image", "data": "([IMAGE DETECTED])", "mime_type": "image/png"},
            {"type": "image", "uri": "gs://bucket/receipt.png", "mime_type": "image/png"},
            {"type": "audio", "data": "[audio]", "mime_type": "audio/wav"},
            {"type": "document", "data": "[file]", "mime_type": "application/pdf"},
        ],
        "questions": {"needs_refund": {"type": "noul", "instructions": "Does the customer ask for a refund?"}},
    }
    assert "secret" not in get_llmobs_input_messages(spans[0])[0]["content"]
    image_parts = get_llmobs_input_messages(spans[0])[0]["image_parts"]
    assert image_parts == [{"mime_type": "image/png", "content": DECISION_IMAGE_DATA}]


def test_decision_raw_response(genai, genai_llmobs, test_spans, decisions_http):
    client = genai.Client()
    raw = client.decisions.with_raw_response.create(
        model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS
    )
    assert raw.parse().answers["needs_refund"].noul == 0.97
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())


def test_decision_streaming_response_not_consumed(genai, genai_llmobs, test_spans, decisions_http):
    client = genai.Client()
    with client.decisions.with_streaming_response.create(
        model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS
    ) as streamed:
        assert streamed.parse().answers["needs_refund"].noul == 0.97
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_span_data(output_messages=[{"role": "assistant", "content": ""}], metrics={}),
    )


def test_decision_error(genai, genai_llmobs, test_spans, decisions_http):
    decisions_http["status"] = 400
    decisions_http["body"] = {"error": {"code": 400, "message": "Invalid question", "status": "INVALID_ARGUMENT"}}
    client = genai.Client()
    with pytest.raises(Exception) as exc_info:
        client.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    span = spans[0]
    assert span.error == 1
    error_type = type(exc_info.value)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        **_expected_span_data(
            output_messages=[{"role": "assistant", "content": ""}],
            metrics={},
            error={
                "type": "%s.%s" % (error_type.__module__, error_type.__name__),
                "message": span.get_tag("error.message"),
                "stack": span.get_tag("error.stack"),
            },
        ),
    )


def test_decision_unpatch(genai, test_spans, decisions_http):
    from ddtrace.contrib.internal.google_genai.patch import patch
    from ddtrace.contrib.internal.google_genai.patch import unpatch

    client = genai.Client()
    unpatch()
    client.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert _spans(test_spans) == []
    patch()
    patch()
    client.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert len(_spans(test_spans)) == 1


def test_shadow_tags_decision_when_llmobs_disabled(genai, test_spans, decisions_http):
    client = genai.Client()
    client.decisions.create(model="decisions-prerelease", input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    span = _spans(test_spans)[0]
    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "decisions-prerelease"
    assert span.get_tag("_dd.llmobs.model_provider") == "google"
    assert span.get_metric("_dd.llmobs.input_tokens") == 120
    assert span.get_metric("_dd.llmobs.total_tokens") == 120
