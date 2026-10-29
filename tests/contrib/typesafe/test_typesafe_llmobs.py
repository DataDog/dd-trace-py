import json
import os

import httpx2
import pytest

from ddtrace.contrib.internal.typesafe.patch import get_version
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_model_provider
from ddtrace.llmobs._utils import get_llmobs_output_messages
from ddtrace.llmobs._utils import get_llmobs_tags
from tests.contrib.typesafe.utils import ERROR_STATE
from tests.contrib.typesafe.utils import EXPECTED_QUESTIONS
from tests.contrib.typesafe.utils import MODEL
from tests.contrib.typesafe.utils import STATE
from tests.contrib.typesafe.utils import build_questions
from tests.llmobs._utils import assert_llmobs_span_data


COMMON_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.typesafe",
    "integration": "typesafe",
    "decision_provider": "typesafe",
}


# Full response bodies recorded in tests/cassettes/typesafe; only the urgency score differs between them
STRING_STATE_RESPONSE = {
    "answers": {
        "refund": {"noul": 0.99, "type": "noul"},
        "team": {
            "choice": "billing",
            "confidence": 1.0,
            "probabilities": {"billing": 1.0, "sales": 0.0, "technical": 0.0},
            "type": "choice",
        },
        "urgency": {
            "confidence": 0.76,
            "legend": {"0": "Low", "1": "Medium", "2": "High"},
            "probabilities": {"0": 0.02, "1": 0.84, "2": 0.14},
            "score": 1.12,
            "type": "score",
        },
    },
    "model": "jev-1.13.0",
    "usage": {"input_tokens": 403, "output_tokens": 69},
}
JSON_STATE_RESPONSE = {
    "answers": {
        "refund": {"noul": 0.99, "type": "noul"},
        "team": {
            "choice": "billing",
            "confidence": 1.0,
            "probabilities": {"billing": 1.0, "sales": 0.0, "technical": 0.0},
            "type": "choice",
        },
        "urgency": {
            "confidence": 0.79,
            "legend": {"0": "Low", "1": "Medium", "2": "High"},
            "probabilities": {"0": 0.02, "1": 0.86, "2": 0.12},
            "score": 1.1,
            "type": "score",
        },
    },
    "model": "jev-1.13.0",
    "usage": {"input_tokens": 417, "output_tokens": 69},
}
STRING_STATE_METRICS = {"input_tokens": 403, "output_tokens": 69, "total_tokens": 472}
JSON_STATE_METRICS = {"input_tokens": 417, "output_tokens": 69, "total_tokens": 486}
STRING_STATE_CASSETTE = "typesafe_v1_systemone_post_8e45a9dd.json"
JSON_STATE_CASSETTE = "typesafe_v1_systemone_post_3b431e67.json"


def _request_body(state):
    return json.dumps({"model": MODEL, "questions": EXPECTED_QUESTIONS, "state": state}, sort_keys=True)


def _recorded_response_text(cassette):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "cassettes", "typesafe", cassette)
    with open(path) as f:
        return json.load(f)["response"]["body"]


def _assert_system_one_span(
    span, state=STATE, response=STRING_STATE_RESPONSE, cassette=STRING_STATE_CASSETTE, metrics=STRING_STATE_METRICS
):
    """The span input is the request body and the output is the response body exactly as received."""
    output = _recorded_response_text(cassette)
    assert json.loads(output) == response
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name=MODEL,
        model_provider="typesafe",
        input_messages=[{"role": "user", "content": _request_body(state)}],
        output_messages=[{"role": "assistant", "content": output}],
        metrics=metrics,
        tags=COMMON_TAGS,
    )


def test_system_one(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    typesafe_client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_positional_args(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    typesafe_client.system_one(STATE, build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_json_state(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    state = {"message": STATE, "plan": "pro"}
    typesafe_client.system_one(state=state, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(
        spans[0], state=state, response=JSON_STATE_RESPONSE, cassette=JSON_STATE_CASSETTE, metrics=JSON_STATE_METRICS
    )


@pytest.mark.asyncio
async def test_system_one_async(typesafe_sdk, async_typesafe_client, typesafe_llmobs, test_spans):
    async with async_typesafe_client as client:
        await client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_error(typesafe_sdk, unauthenticated_typesafe_client, typesafe_llmobs, test_spans):
    with pytest.raises(typesafe_sdk.TypeSafeAuthenticationError):
        unauthenticated_typesafe_client.system_one(
            state=ERROR_STATE, questions=build_questions(typesafe_sdk), model=MODEL
        )
    spans = test_spans.pop()
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL,
        model_provider="typesafe",
        input_messages=[{"role": "user", "content": _request_body(ERROR_STATE)}],
        output_messages=[{"content": ""}],
        error={
            "type": "typesafe_sdk._core.errors.TypeSafeAuthenticationError",
            "message": spans[0].get_tag("error.message"),
            "stack": spans[0].get_tag("error.stack"),
        },
        tags=COMMON_TAGS,
    )
    assert spans[0].error == 1


@pytest.mark.subprocess(env={"DD_API_KEY": "<not-a-real-key>"})
def test_llmobs_enable_patches_typesafe():
    """LLMObs.enable() with default integrations patches typesafe-sdk, and a bad registration name breaks it."""
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(ml_app="<ml-app-name>", agentless_enabled=False)
    import typesafe_sdk

    assert LLMObs.enabled
    assert getattr(typesafe_sdk, "_datadog_patch", False)
    LLMObs.disable()


STUB_RESPONSE = json.dumps(
    {"model": "jev-1.13.0", "answers": {"refund": {"type": "noul", "noul": 0.5}}, "usage": {"input_tokens": 10}}
)
# Only the PNG signature: the SDK sniffs it for the content type and never decodes the image
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(16)


class _RecordingTransport(httpx2.BaseTransport):
    def __init__(self):
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=STUB_RESPONSE.encode())


def _sent_json_body(request):
    """Return the JSON body the SDK sent, which is the "request" part of a multipart body when images are attached."""
    content_type = request.headers["content-type"]
    if not content_type.startswith("multipart/form-data"):
        return json.loads(request.content)
    boundary = content_type.split("boundary=")[1].encode()
    for part in request.content.split(b"--" + boundary):
        headers, _, body = part.partition(b"\r\n\r\n")
        if b'name="request"' in headers:
            return json.loads(body.rstrip(b"\r\n"))
    raise AssertionError("no request part in the multipart body")


def _call_with_recording_transport(typesafe_sdk, test_spans, attach=(), client_kwargs=None, **kwargs):
    transport = _RecordingTransport()
    with typesafe_sdk.TypeSafeClient(
        api_key="not-a-real-key",
        transport=transport,
        retry=typesafe_sdk.RetryPolicy(max_retries=0),
        **(client_kwargs or {}),
    ) as client:
        if attach:
            with client.attach_assets(list(attach)):
                client.system_one(model=MODEL, **kwargs)
        else:
            client.system_one(model=MODEL, **kwargs)
    spans = test_spans.pop()
    assert len(spans) == 1
    assert len(transport.requests) == 1
    input_body = json.loads(get_llmobs_input_messages(spans[0])[0]["content"])
    output = get_llmobs_output_messages(spans[0])[0]["content"]
    return spans[0], input_body, _sent_json_body(transport.requests[0]), output


@pytest.mark.parametrize(
    "overrides",
    [{}, {"extra_body": {"model": "jev-latest", "debug": True}}, {"state": range(3)}],
    ids=["plain", "extra_body", "sequence_state"],
)
def test_system_one_span_io_matches_http_bodies(typesafe_sdk, typesafe_llmobs, test_spans, overrides):
    kwargs = {"state": STATE, "questions": build_questions(typesafe_sdk), **overrides}
    span, input_body, sent_body, output = _call_with_recording_transport(typesafe_sdk, test_spans, **kwargs)
    assert input_body == sent_body
    assert output == STUB_RESPONSE
    assert span.get_tag("typesafe.request.model") == sent_body["model"]


def test_system_one_images_recorded_as_references(typesafe_sdk, typesafe_llmobs, test_spans):
    """Images appear as their alias or reference, as in the request body; their bytes are not recorded."""
    if not hasattr(typesafe_sdk, "Image"):
        pytest.skip("images require typesafe-sdk>=0.7.5")
    photo = typesafe_sdk.Image(PNG_BYTES)
    chart = typesafe_sdk.Image(PNG_BYTES)
    _, input_body, sent_body, _ = _call_with_recording_transport(
        typesafe_sdk,
        test_spans,
        state={"message": "My order arrived broken.", "photo": photo},
        questions={"damage": {"type": "noul", "instructions": ["Is the item in the chart damaged?", chart]}},
        assets={"photo": photo},
    )
    assert input_body == sent_body
    assert input_body["state"]["photo"] == "photo"
    assert input_body["questions"]["damage"]["instructions"][1] == str(chart)
    assert input_body["assets"] == {
        "photo": {"type": "image", "part": "asset-0"},
        str(chart): {"type": "image", "part": "asset-1"},
    }


@pytest.mark.parametrize("kind", ["extra_allow", "root_model"])
def test_system_one_custom_response_model(typesafe_sdk, typesafe_llmobs, test_spans, kind):
    """A custom response_model has no raw HTTP response, so the parsed model is recorded as the model serializes it."""
    if parse_version(get_version()) < (0, 7):
        pytest.skip("response_model requires typesafe-sdk>=0.7")
    pydantic = pytest.importorskip("pydantic")

    class AnyResponse(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="allow")

    response_model = AnyResponse if kind == "extra_allow" else pydantic.RootModel[dict]
    _, _, _, output = _call_with_recording_transport(
        typesafe_sdk, test_spans, state=STATE, questions=build_questions(typesafe_sdk), response_model=response_model
    )
    assert json.loads(output) == json.loads(STUB_RESPONSE)


def test_system_one_image_under_several_names(typesafe_sdk, typesafe_llmobs, test_spans):
    """The SDK uploads an image once per name it is passed under, so every name stays in the assets manifest."""
    if not hasattr(typesafe_sdk, "Image"):
        pytest.skip("images require typesafe-sdk>=0.7.5")
    photo = typesafe_sdk.Image(PNG_BYTES)
    _, input_body, sent_body, _ = _call_with_recording_transport(
        typesafe_sdk,
        test_spans,
        state=["My order arrived broken.", photo],
        questions=build_questions(typesafe_sdk),
        assets={"original": photo, "thumbnail": photo},
    )
    assert input_body == sent_body
    assert input_body["assets"] == {
        "original": {"type": "image", "part": "asset-0"},
        "thumbnail": {"type": "image", "part": "asset-1"},
    }


def test_system_one_attached_image_referenced_in_text(typesafe_sdk, typesafe_llmobs, test_spans):
    """Images attached to the client and referenced from text are listed under assets, as the SDK uploads them."""
    if not hasattr(typesafe_sdk, "Image"):
        pytest.skip("images require typesafe-sdk>=0.7.5")
    photo = typesafe_sdk.Image(PNG_BYTES)
    placed = typesafe_sdk.Image(PNG_BYTES)
    _, input_body, sent_body, _ = _call_with_recording_transport(
        typesafe_sdk,
        test_spans,
        attach=[photo],
        state=[f"The customer says {photo} arrived scratched.", placed],
        questions=build_questions(typesafe_sdk),
    )
    assert input_body == sent_body
    assert input_body["assets"] == {
        str(photo): {"type": "image", "part": "asset-0"},
        str(placed): {"type": "image", "part": "asset-1"},
    }


def test_system_one_openrouter_decision_provider(typesafe_sdk, typesafe_llmobs, test_spans):
    span, _, _, _ = _call_with_recording_transport(
        typesafe_sdk,
        test_spans,
        client_kwargs={"base_url": "https://openrouter.ai/api/v1"},
        state=STATE,
        questions=build_questions(typesafe_sdk),
    )
    assert get_llmobs_model_provider(span) == "openrouter"
    assert get_llmobs_tags(span)["decision_provider"] == "typesafe"
