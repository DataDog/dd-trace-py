import json

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

MODEL = "gemini-decision-1"
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
# Response body of the Decisions API for the request above, as received
DECISION_RESPONSE_BODY = (
    '{"answers":{"needs_refund":{"confidence":0.99,"noul":0.99,"type":"noul"},"topic":{"confidence":0.9946781796634923,'
    '"choice":"billing","probabilities":{"other":0.0028879477322337047,"billing":0.9946781796634923,'
    '"shipping":0.002433872604274023},"type":"choice"},"urgency":{"confidence":0.5055514210087809,'
    '"score":1.4444585446910128,"probabilities":{"0":0.061092876317768066,"2":0.5055514210087809,'
    '"1":0.4333557026734509},"legend":{"0":"low","2":"high","1":"medium"},"type":"score"}},"usage":{"total_tokens":213,'
    '"total_input_tokens":213,"input_tokens_by_modality":[{"modality":"text","tokens":213}],"total_output_tokens":0}}'
)
DECISION_ANSWERS = json.loads(DECISION_RESPONSE_BODY)["answers"]
DECISION_ERROR_BODY = '{"error":{"code":400,"message":"Invalid question","status":"INVALID_ARGUMENT"}}'
DECISION_IMAGE_DATA = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
COMMON_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.google_genai",
    "integration": "google_genai",
    "decision_provider": "google",
}


@pytest.fixture
def decisions_http():
    """Serve Decisions API responses from a mock transport and record the JSON bodies sent."""
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx

    class _UnreadStream(httpx.SyncByteStream, httpx.AsyncByteStream):
        """A body that is read only when the client reads it, as from the network."""

        def __init__(self, content):
            self._content = content

        def __iter__(self):
            yield self._content

        async def __aiter__(self):
            yield self._content

    state = {"bodies": [], "status": 200, "body": DECISION_RESPONSE_BODY}

    def _handler(request):
        state["bodies"].append(json.loads(request.content))
        return httpx.Response(
            state["status"],
            headers={"content-type": "application/json"},
            stream=_UnreadStream(state["body"].encode()),
        )

    state["http_options"] = {
        "httpx_client": httpx.Client(transport=httpx.MockTransport(_handler)),
        "httpx_async_client": httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    }
    return state


@pytest.fixture
def decisions_client(genai, decisions_http):
    return genai.Client(http_options=decisions_http["http_options"])


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
        "model_name": MODEL,
        "model_provider": "google",
        "input_messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {"model": MODEL, "input": DECISION_INPUT, "questions": DECISION_QUESTIONS},
                    sort_keys=True,
                ),
            }
        ],
        "output_messages": [
            {"role": "assistant", "content": json.dumps({"answers": DECISION_ANSWERS}, sort_keys=True)}
        ],
        "metrics": {"input_tokens": 213, "output_tokens": 0, "total_tokens": 213},
        "tags": COMMON_TAGS,
    }
    kwargs.update(overrides)
    return kwargs


def test_decision(decisions_client, genai_llmobs, test_spans, decisions_http):
    client = decisions_client
    response = client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert response.answers["topic"].choice == "billing"
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].resource == "Decisions.create"
    assert spans[0].get_tag("google_genai.request.provider") == "google"
    assert spans[0].get_tag("google_genai.request.model") == MODEL
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())
    request, response_body = _decision_io(spans[0])
    assert request == decisions_http["bodies"][0]
    assert response_body == {"answers": DECISION_ANSWERS}


async def test_decision_async(decisions_client, genai_llmobs, test_spans, decisions_http):
    client = decisions_client
    await client.aio.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].resource == "AsyncDecisions.create"
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())


def test_decision_model_path(decisions_client, genai_llmobs, test_spans, decisions_http):
    """The model is recorded without its models/ prefix, and the request keeps it as sent."""
    client = decisions_client
    client.decisions.create(model="models/" + MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    spans = _spans(test_spans)
    assert _get_llmobs_data_metastruct(spans[0])["meta"]["model_name"] == MODEL
    request, _ = _decision_io(spans[0])
    assert request["model"] == "models/" + MODEL


def test_decision_media_and_extra_body(decisions_client, genai_llmobs, test_spans, decisions_http):
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
    client = decisions_client
    client.decisions.create(
        model=MODEL,
        input=decision_input,
        questions=questions,
        extra_body={"model": "gemini-decision-2", "api_token": "secret"},
    )
    spans = _spans(test_spans)
    assert len(spans) == 1
    request, _ = _decision_io(spans[0])
    assert request == {
        "model": "gemini-decision-2",
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


def test_decision_raw_response(decisions_client, genai_llmobs, test_spans, decisions_http):
    client = decisions_client
    raw = client.decisions.with_raw_response.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert raw.parse().answers["needs_refund"].noul == 0.99
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), **_expected_span_data())


def test_decision_streaming_response_not_consumed(decisions_client, genai_llmobs, test_spans, decisions_http):
    client = decisions_client
    with client.decisions.with_streaming_response.create(
        model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS
    ) as streamed:
        assert streamed.parse().answers["needs_refund"].noul == 0.99
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        **_expected_span_data(output_messages=[{"role": "assistant", "content": ""}], metrics={}),
    )


def test_decision_error(decisions_client, genai_llmobs, test_spans, decisions_http):
    decisions_http["status"] = 400
    decisions_http["body"] = DECISION_ERROR_BODY
    client = decisions_client
    with pytest.raises(Exception) as exc_info:
        client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
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


def test_decision_unpatch(decisions_client, test_spans):
    from ddtrace.contrib.internal.google_genai.patch import patch
    from ddtrace.contrib.internal.google_genai.patch import unpatch

    client = decisions_client
    unpatch()
    client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert _spans(test_spans) == []
    patch()
    patch()
    client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    assert len(_spans(test_spans)) == 1


def test_shadow_tags_decision_when_llmobs_disabled(decisions_client, test_spans):
    client = decisions_client
    client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    span = _spans(test_spans)[0]
    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == MODEL
    assert span.get_tag("_dd.llmobs.model_provider") == "google"
    assert span.get_metric("_dd.llmobs.input_tokens") == 213
    assert span.get_metric("_dd.llmobs.total_tokens") == 213


def test_decision_cached_tokens(decisions_client, genai_llmobs, test_spans, decisions_http):
    body = json.loads(DECISION_RESPONSE_BODY)
    body["usage"]["total_cached_tokens"] = 64
    decisions_http["body"] = json.dumps(body)
    decisions_client.decisions.create(model=MODEL, input=DECISION_INPUT, questions=DECISION_QUESTIONS)
    metrics = _get_llmobs_data_metastruct(_spans(test_spans)[0])["metrics"]
    assert metrics["cache_read_input_tokens"] == 64
