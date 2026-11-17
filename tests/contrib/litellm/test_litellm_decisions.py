import json
import sys

import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_output_value
from tests.llmobs._utils import assert_llmobs_span_data


litellm_module = pytest.importorskip("litellm")
pytestmark = pytest.mark.skipif(not hasattr(litellm_module, "decisions"), reason="litellm has no decisions API")

TICKET = "I was charged twice for my subscription this month and want the duplicate refunded."
OPENAI_QUESTIONS = [
    {"type": "predicate", "name": "needs_refund", "instructions": "Is the customer asking for a refund?"}
]
SYSTEM_ONE_QUESTIONS = {"needs_refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"}}
TAGS = {"ml_app": "<ml-app-name>", "integration": "litellm"}
requires_systemone = pytest.mark.skipif(not hasattr(litellm_module, "systemone"), reason="litellm has no systemone")


@pytest.fixture(autouse=True)
def typesafe_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "<not-a-real-key>")


def _spans(test_spans):
    return [s for trace in test_spans.pop_traces() for s in trace]


def _content(message):
    return json.loads(message["content"])


def _input_body(span):
    return _content(_get_llmobs_data_metastruct(span)["meta"]["input"]["messages"][0])


def _output_body(span):
    return _content(_get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0])


def _assert_openai_body(span, model):
    assert _input_body(span) == {"model": model, "input": TICKET, "questions": OPENAI_QUESTIONS}
    output = _output_body(span)
    assert "usage" not in output
    assert output["answers"][0]["name"] == "needs_refund"


def test_decisions_openai(litellm, request_vcr, litellm_llmobs, test_spans):
    with request_vcr.use_cassette("decisions_openai.yaml"):
        litellm.decisions(model="openai/gpt-6-luna", input=TICKET, questions=OPENAI_QUESTIONS, api_key="sk-secret")

    spans = _spans(test_spans)
    assert len(spans) == 1
    span = spans[0]
    assert span.resource == "decisions"
    assert span.get_tag("litellm.request.model") == "openai/gpt-6-luna"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="gpt-6-luna",
        model_provider="openai",
        metrics={"input_tokens": 171, "output_tokens": 0, "total_tokens": 171},
        tags={**TAGS, "decision_provider": "openai"},
    )
    _assert_openai_body(span, "openai/gpt-6-luna")
    assert "sk-secret" not in json.dumps(_get_llmobs_data_metastruct(span), default=str)


async def test_adecisions_typesafe(litellm, request_vcr, litellm_llmobs, test_spans):
    with request_vcr.use_cassette("adecisions_typesafe.yaml"):
        await litellm.adecisions(model="typesafe/jev-latest", input=TICKET, questions=OPENAI_QUESTIONS)

    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].resource == "adecisions"
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name="jev-latest",
        model_provider="typesafe",
        tags={**TAGS, "decision_provider": "openai"},
    )
    _assert_openai_body(spans[0], "typesafe/jev-latest")


def _assert_system_one(span, resource):
    assert span.resource == resource
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="jev-latest",
        model_provider="typesafe",
        tags={**TAGS, "decision_provider": "typesafe"},
    )
    assert _input_body(span) == {
        "model": "typesafe/jev-latest",
        "state": {"ticket": TICKET},
        "questions": SYSTEM_ONE_QUESTIONS,
    }
    output = _output_body(span)
    assert "usage" not in output
    assert output["answers"]["needs_refund"]["type"] == "noul"
    metrics = _get_llmobs_data_metastruct(span)["metrics"]
    assert metrics["input_tokens"] > 0
    assert metrics["total_tokens"] == metrics["input_tokens"] + metrics["output_tokens"]


@requires_systemone
def test_systemone(litellm, request_vcr, litellm_llmobs, test_spans):
    with request_vcr.use_cassette("systemone_typesafe.yaml"):
        litellm.systemone(model="typesafe/jev-latest", state={"ticket": TICKET}, questions=SYSTEM_ONE_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    _assert_system_one(spans[0], "systemone")


@requires_systemone
async def test_asystemone(litellm, request_vcr, litellm_llmobs, test_spans):
    with request_vcr.use_cassette("asystemone_typesafe.yaml"):
        await litellm.asystemone(model="typesafe/jev-latest", state={"ticket": TICKET}, questions=SYSTEM_ONE_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    _assert_system_one(spans[0], "asystemone")


def _decision_router(litellm):
    return litellm.Router(model_list=[{"model_name": "decider", "litellm_params": {"model": "openai/gpt-6-luna"}}])


def _assert_router_spans(spans, operation):
    assert len(spans) == 2
    router_span, llm_span = spans
    assert llm_span.parent_id == router_span.span_id
    router_data = _get_llmobs_data_metastruct(router_span)
    assert_llmobs_span_data(router_data, span_kind="workflow", tags=TAGS)
    assert "decision_provider" not in router_data.get("tags", {})
    assert not router_data.get("metrics")
    assert json.loads(get_llmobs_input_value(router_span))["model"] == "decider"
    assert json.loads(get_llmobs_output_value(router_span))["answers"][0]["name"] == "needs_refund"
    assert get_llmobs_metadata(router_span)["router_settings"]["model_list"] == [
        {"model_name": "decider", "litellm_params": {"model": "openai/gpt-6-luna"}}
    ]
    assert router_span.resource == operation
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(llm_span),
        span_kind="llm",
        model_name="gpt-6-luna",
        model_provider="openai",
        metrics={"input_tokens": 171, "output_tokens": 0, "total_tokens": 171},
        tags={**TAGS, "decision_provider": "openai"},
    )


def test_router_decisions(litellm, request_vcr, litellm_llmobs, test_spans):
    router = _decision_router(litellm)
    with request_vcr.use_cassette("router_decisions.yaml"):
        router.decisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
    _assert_router_spans(_spans(test_spans), "router.decisions")


async def test_router_adecisions(litellm, request_vcr, litellm_llmobs, test_spans):
    router = _decision_router(litellm)
    with request_vcr.use_cassette("router_adecisions.yaml"):
        await router.adecisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
    _assert_router_spans(_spans(test_spans), "router.adecisions")


def test_decisions_error(litellm, request_vcr, litellm_llmobs, test_spans):
    with request_vcr.use_cassette("decisions_error.yaml"):
        with pytest.raises(Exception):
            litellm.decisions(model="openai/not-a-decision-model", input=TICKET, questions=OPENAI_QUESTIONS)
    spans = _spans(test_spans)
    assert len(spans) == 1
    assert spans[0].error == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_provider="openai",
        output_messages=[{"content": ""}],
        error={
            "type": spans[0].get_tag("error.type"),
            "message": spans[0].get_tag("error.message"),
            "stack": spans[0].get_tag("error.stack"),
        },
        tags={**TAGS, "decision_provider": "openai"},
    )


def test_bind_arguments_positional():
    from ddtrace.contrib.internal.litellm.patch import _bind_arguments

    def decisions_v1_104(model, state=None, questions=None, input=None, **kwargs):  # noqa: A002
        pass

    assert _bind_arguments(decisions_v1_104, ("typesafe/jev", {"a": 1}), {"api_key": "k"}) == {
        "model": "typesafe/jev",
        "state": {"a": 1},
        "api_key": "k",
    }


def test_system_one_format_detection():
    from ddtrace.llmobs._integrations.litellm import is_system_one_request

    assert is_system_one_request({}, "systemone")
    assert is_system_one_request({}, "router.asystemone")
    assert is_system_one_request({"state": {}}, "decisions")
    assert not is_system_one_request({"input": "x"}, "decisions")
    assert not is_system_one_request({"input": "x", "state": None}, "adecisions")


def test_system_one_inline_image_is_not_recorded():
    from ddtrace.llmobs._integrations.litellm import _system_one_media

    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    parts = []
    result = _system_one_media(
        [f"data:image/png;base64,{png}", {"content_type": "image/png", "base64": png}, "https://example.com/a.png"],
        parts,
    )
    assert png not in json.dumps(result)
    assert len(parts) == 2
    assert result[2] == "https://example.com/a.png"


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


@requires_systemone
def test_systemone_assets_are_image_parts(litellm, request_vcr, litellm_llmobs, test_spans):
    from litellm.types.decisions import DecisionsAsset

    assets = {
        "photo": {"type": "image", "format": "png", "data_base64": PNG},
        "scan": DecisionsAsset(type="image", format="jpg", data_base64=PNG),
    }
    with request_vcr.use_cassette("systemone_typesafe.yaml"):
        litellm.systemone(
            model="typesafe/jev-latest",
            state={"ticket": TICKET},
            questions=SYSTEM_ONE_QUESTIONS,
            assets=assets,
            drop_params=True,
        )
    span = _spans(test_spans)[0]
    message = _get_llmobs_data_metastruct(span)["meta"]["input"]["messages"][0]
    assert PNG not in json.dumps(message["content"])
    assert json.loads(message["content"])["assets"] == {"photo": "([IMAGE DETECTED])", "scan": "([IMAGE DETECTED])"}
    assert [part["mime_type"] for part in message["image_parts"]] == ["image/png", "image/jpeg"]


def test_system_one_oversized_asset_is_dropped():
    from ddtrace.llmobs._integrations.litellm import _system_one_media

    big = "A" * (5 * 1024 * 1024)
    parts = []
    assert _system_one_media({"a": {"format": "png", "data_base64": big}}, parts) == {"a": "[image omitted: too large]"}
    assert parts == []


def test_router_decisions_after_unpatch_and_repatch(litellm, request_vcr, litellm_llmobs, test_spans):
    from ddtrace.contrib.internal.litellm.patch import patch
    from ddtrace.contrib.internal.litellm.patch import unpatch

    router = _decision_router(litellm)
    unpatch()
    with request_vcr.use_cassette("router_decisions.yaml", allow_playback_repeats=True):
        router.decisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
        litellm.decisions(model="openai/gpt-6-luna", input=TICKET, questions=OPENAI_QUESTIONS)
    assert _spans(test_spans) == []
    patch()
    with request_vcr.use_cassette("router_decisions.yaml"):
        router.decisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
    assert [s.resource for s in _spans(test_spans)] == ["router.decisions", "decisions"]
    with request_vcr.use_cassette("router_decisions.yaml"):
        _decision_router(litellm).decisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
    assert [s.resource for s in _spans(test_spans)] == ["router.decisions", "decisions"]


async def test_router_adecisions_after_unpatch_and_repatch(litellm, request_vcr, litellm_llmobs, test_spans):
    from ddtrace.contrib.internal.litellm.patch import patch
    from ddtrace.contrib.internal.litellm.patch import unpatch

    router = _decision_router(litellm)
    unpatch()
    with request_vcr.use_cassette("router_adecisions.yaml", allow_playback_repeats=True):
        await router.adecisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
        await litellm.adecisions(model="openai/gpt-6-luna", input=TICKET, questions=OPENAI_QUESTIONS)
    assert _spans(test_spans) == []
    patch()
    with request_vcr.use_cassette("router_adecisions.yaml"):
        await router.adecisions(model="decider", input=TICKET, questions=OPENAI_QUESTIONS)
    assert [s.resource for s in _spans(test_spans)] == ["router.adecisions", "adecisions"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="litellm decisions need Python 3.10+")
def test_decision_functions_patched(litellm):
    for name in ("decisions", "adecisions"):
        assert hasattr(getattr(litellm, name), "__wrapped__")
        assert hasattr(getattr(sys.modules["litellm.decisions"], name), "__wrapped__")
