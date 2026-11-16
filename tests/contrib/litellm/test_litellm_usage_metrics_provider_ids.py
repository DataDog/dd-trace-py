"""LiteLLM usage metrics: the provider account and project that billed a call, the operator's ids from model_info,
and the service tier the provider served.
"""

import litellm
import pytest

from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_REQUEST
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics import TOKEN_BREAKDOWN
from ddtrace.contrib.internal.litellm._usage_metrics import UsageMetricsLogger
from ddtrace.internal.ai_usage import ai_usage
from tests.contrib.litellm.test_litellm_usage_metrics import ANTHROPIC_USAGE
from tests.contrib.litellm.test_litellm_usage_metrics import CHAT_USAGE
from tests.contrib.litellm.test_litellm_usage_metrics import T0
from tests.contrib.litellm.test_litellm_usage_metrics import NativeWriter
from tests.contrib.litellm.test_litellm_usage_metrics import Proxy
from tests.contrib.litellm.test_litellm_usage_metrics import RecordingWriter
from tests.contrib.litellm.test_litellm_usage_metrics import UserAPIKeyAuth
from tests.contrib.litellm.test_litellm_usage_metrics import at
from tests.contrib.litellm.test_litellm_usage_metrics import dogstatsd_lines
from tests.contrib.litellm.test_litellm_usage_metrics import run


@pytest.fixture
def writer():
    writer = NativeWriter() if ai_usage is not None else RecordingWriter()
    yield writer
    assert getattr(writer, "rejected", []) == []


@pytest.fixture
def logger(writer):
    return UsageMetricsLogger(writer, frozenset(), None)


ACCOUNT = "trajectory.request.provider_account_id"
PROJECT = "trajectory.request.project_id"
KEY_ID = "trajectory.request.api_key_id"
PROVIDER_ID_TAGS = frozenset({"provider_account", "project"})
OPENAI_HEADERS = {"openai-organization": "acme-staging", "openai-project": "proj_abc123", "x-request-id": "req_1"}
ANTHROPIC_HEADERS = {"anthropic-organization-id": "4257e925-ee99-4ee8-9c62-8e53716d5203", "x-request-id": "req_1"}


class Response:
    """A LiteLLM response, with the provider's headers kept in its hidden params."""

    def __init__(self, headers):
        self._hidden_params = {"additional_headers": {f"llm_provider-{k}": v for k, v in headers.items()}}


class HttpxResponse:
    def __init__(self, headers):
        import httpx

        self.headers = httpx.Headers(headers)


def _prefixed(headers):
    return {f"llm_provider-{k}": v for k, v in headers.items()}


# Where each LiteLLM version and route keeps the provider's response headers for the callback.
HEADER_SOURCES = {
    "response_hidden_params": lambda h: {"response": Response(h)},
    "payload_hidden_params": lambda h: {"payload_extra": {"hidden_params": {"additional_headers": _prefixed(h)}}},
    "response_headers": lambda h: {"kwargs_extra": {"response_headers": dict(h)}},
    # Native Anthropic Messages streams keep them on the raw response only, whose header names have any case.
    "httpx_response": lambda h: {
        "kwargs_extra": {"httpx_response": HttpxResponse({k.title(): v for k, v in h.items()})}
    },
}


def _attributes_by_profile(writer):
    return {profile: attributes for profile, _, attributes in writer.records}


@pytest.mark.parametrize("source", sorted(HEADER_SOURCES))
@pytest.mark.parametrize(
    "provider,headers,expected",
    [
        ("openai", OPENAI_HEADERS, {ACCOUNT: "acme-staging", PROJECT: "proj_abc123"}),
        ("anthropic", ANTHROPIC_HEADERS, {ACCOUNT: "4257e925-ee99-4ee8-9c62-8e53716d5203"}),
    ],
)
def test_provider_ids_come_from_the_provider_response_headers(writer, source, provider, headers, expected):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider=provider)
    proxy.succeeded(1, **HEADER_SOURCES[source](headers))

    # On the attempt's points, and on its gateway request, of which it is the last attempt. No other header is read.
    assert _attributes_by_profile(writer) == {
        PROVIDER_ATTEMPT: expected,
        TOKEN_BREAKDOWN: expected,
        GATEWAY_REQUEST: expected,
    }


def test_provider_ids_from_headers_are_opt_in(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1, response=Response(OPENAI_HEADERS))
    assert all(attributes == {} for _, _, attributes in writer.records)


def test_each_provider_id_tag_enables_its_own_attribute(writer):
    logger = UsageMetricsLogger(writer, frozenset({"project"}), None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1, response=Response(OPENAI_HEADERS))
    assert all(attributes == {PROJECT: "proj_abc123"} for _, _, attributes in writer.records)


@pytest.mark.parametrize(
    "provider,headers",
    [
        # No header: the attribute is omitted, never sent empty.
        ("openai", {}),
        ("openai", {"openai-organization": "", "openai-project": ""}),
        ("openai", {"openai-organization": " ", "openai-project": "p" * 129}),
        # A header is read only from the provider that defines it.
        ("anthropic", OPENAI_HEADERS),
        ("openai", ANTHROPIC_HEADERS),
        ("azure", OPENAI_HEADERS),
    ],
)
def test_missing_or_foreign_headers_give_no_provider_ids(writer, provider, headers):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider=provider)
    proxy.succeeded(1, response=Response(headers), kwargs_extra={"response_headers": headers})
    assert writer.records
    assert all(attributes == {} for _, _, attributes in writer.records)


@pytest.mark.parametrize(
    "model,account",
    [
        (
            "arn:aws:bedrock:us-east-1:202749265359:inference-profile/us.anthropic.claude-sonnet-4-5-v1:0",
            "202749265359",
        ),
        ("converse/arn:aws:bedrock:us-east-1:202749265359:application-inference-profile/abc123", "202749265359"),
        ("arn:aws-us-gov:bedrock:us-gov-west-1:123456789012:inference-profile/us-gov.anthropic.x", "123456789012"),
        # A foundation model's ARN names no account.
        ("arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0", None),
        ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", None),
    ],
)
def test_a_bedrock_model_arn_names_the_aws_account(writer, model, account):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider="bedrock", model=model)
    proxy.succeeded(1, model=model)
    expected = {ACCOUNT: account} if account else {}
    assert writer.records
    assert all(attributes == expected for _, _, attributes in writer.records)


def test_a_bedrock_model_id_arn_names_the_aws_account(writer):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider="bedrock", model="anthropic.claude-sonnet-4-5-20250929-v1:0")
    arn = "arn:aws:bedrock:us-east-1:202749265359:application-inference-profile/abc123"
    proxy.succeeded(1, kwargs_extra={"model_id": arn})
    assert all(attributes == {ACCOUNT: "202749265359"} for _, _, attributes in writer.records)


OPERATOR_INFO = {
    "datadog_api_key_id": "key_01H8XYZ",
    "datadog_provider_account_id": "org-operator",
    "datadog_project_id": "proj_operator",
}
OPERATOR_IDS = {KEY_ID: "key_01H8XYZ", ACCOUNT: "org-operator", PROJECT: "proj_operator"}


@pytest.mark.parametrize("tags", [frozenset(), PROVIDER_ID_TAGS])
def test_operator_ids_from_model_info_are_sent_and_win(writer, tags):
    logger = UsageMetricsLogger(writer, tags, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model_info=OPERATOR_INFO)
    proxy.succeeded(1, response=Response(OPENAI_HEADERS))
    assert _attributes_by_profile(writer) == {
        PROVIDER_ATTEMPT: OPERATOR_IDS,
        TOKEN_BREAKDOWN: OPERATOR_IDS,
        GATEWAY_REQUEST: OPERATOR_IDS,
    }


def test_operator_ids_fill_in_what_the_headers_do_not_say(writer):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider="anthropic", model_info={"datadog_project_id": "wrkspc_01"})
    proxy.succeeded(1, response=Response(ANTHROPIC_HEADERS))
    expected = {ACCOUNT: ANTHROPIC_HEADERS["anthropic-organization-id"], PROJECT: "wrkspc_01"}
    assert all(attributes == expected for _, _, attributes in writer.records)


@pytest.mark.parametrize("value", ["", "   ", "k" * 129, 42, None, ["key_1"], {"id": "key_1"}])
def test_unusable_operator_ids_are_left_out(logger, writer, value):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model_info={key: value for key in OPERATOR_INFO})
    proxy.succeeded(1)
    assert all(attributes == {} for _, _, attributes in writer.records)


def test_operator_ids_are_on_failed_attempts_and_requests(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model_info=OPERATOR_INFO)
    proxy.attempt_failed(litellm.InternalServerError("boom", "openai", "gpt-4o-mini"))
    proxy.failed(litellm.InternalServerError("boom", "openai", "gpt-4o-mini"))
    assert _attributes_by_profile(writer) == {PROVIDER_ATTEMPT: OPERATOR_IDS, GATEWAY_REQUEST: OPERATOR_IDS}


def test_the_gateway_request_has_the_provider_ids_of_its_last_attempt(writer):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model_id="primary", model_info={"datadog_provider_account_id": "acct-primary"})
    proxy.attempt_failed(litellm.InternalServerError("boom", "openai", "gpt-4o-mini"))
    proxy.attempt(1, model_id="fallback", model_info={"datadog_provider_account_id": "acct-fallback"})
    proxy.succeeded(2)
    failed, succeeded = writer.of(PROVIDER_ATTEMPT)
    assert failed[1] == {ACCOUNT: "acct-primary"}
    assert succeeded[1] == {ACCOUNT: "acct-fallback"}
    [(_, request)] = writer.of(GATEWAY_REQUEST)
    assert request == {ACCOUNT: "acct-fallback"}


def test_a_cache_hit_has_no_provider_ids(writer):
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.succeeded(0.1, usage=CHAT_USAGE, cost=0.0, cache_hit=True, response=Response(OPENAI_HEADERS))
    [(_, attributes)] = writer.of(GATEWAY_REQUEST)
    assert attributes == {}


@pytest.mark.parametrize(
    "payload_extra,usage,tier",
    [
        # OpenAI's shapes, and Bedrock: the response's own tier.
        ({"response": {"service_tier": "default"}}, CHAT_USAGE, "default"),
        ({"response": {"service_tier": "flex"}}, dict(CHAT_USAGE, service_tier="priority"), "flex"),
        # Anthropic: the usage object's.
        ({"response": {}}, dict(ANTHROPIC_USAGE, service_tier="standard"), "standard"),
        ({}, dict(ANTHROPIC_USAGE, service_tier="standard"), "standard"),
        ({"response": {"service_tier": None}}, dict(ANTHROPIC_USAGE, service_tier="batch"), "batch"),
        # No tier, or one outside the grammar the native module accepts: no tier.
        ({}, CHAT_USAGE, None),
        ({"response": {"service_tier": ""}}, CHAT_USAGE, None),
        ({"response": {"service_tier": "on demand"}}, CHAT_USAGE, None),
        ({"response": {"service_tier": "t" * 33}}, CHAT_USAGE, None),
        ({"response": {"service_tier": 1}}, CHAT_USAGE, None),
    ],
)
def test_service_tier_is_the_tier_the_provider_served(logger, writer, payload_extra, usage, tier):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    # The tier the request asked for is never the source.
    proxy.succeeded(1, usage=usage, payload_extra=dict(payload_extra, model_parameters={"service_tier": "priority"}))
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    [(breakdown, _)] = writer.of(TOKEN_BREAKDOWN)
    assert attempt.get("service_tier") == breakdown.get("service_tier") == tier
    if tier is None:
        assert "service_tier" not in attempt


@pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")
def test_native_points_carry_the_service_tier_and_provider_ids():
    writer = NativeWriter()
    logger = UsageMetricsLogger(writer, PROVIDER_ID_TAGS, None)
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model_info={"datadog_api_key_id": "key_01H8XYZ"})
    proxy.succeeded(1, response=Response(OPENAI_HEADERS), payload_extra={"response": {"service_tier": "default"}})
    assert writer.rejected == []
    lines = dogstatsd_lines(writer.metrics)

    def tags(name):
        return [set(line.split("|#", 1)[1].split(",")) for line in lines if line.startswith(name + ":")]

    ids = {
        "trajectory.request.provider_account_id:acme-staging",
        "trajectory.request.project_id:proj_abc123",
        "trajectory.request.api_key_id:key_01h8xyz",
    }
    # Every series carries the ids; the tier is only on the token and cost counters.
    for name in (
        "gen_ai.client.inference.usage.input_tokens",
        "gen_ai.client.inference.usage.cache_read.input_tokens",
        "trajectory.gen_ai.client.inference.usage.cost",
    ):
        assert tags(name) and all(ids | {"trajectory.request.service_tier:default"} <= t for t in tags(name)), name
    for name in ("gen_ai.client.inference.duration.count", "trajectory.gen_ai.gateway.request.estimated_cost"):
        assert tags(name) and all(ids <= t and not any("service_tier" in tag for tag in t) for t in tags(name)), name


@pytest.mark.parametrize(
    "where",
    [
        lambda info: {"model_info": info},
        lambda info: {"metadata": {"model_info": info}},
        # The Anthropic Messages and Responses routes before LiteLLM 1.104.
        lambda info: {"litellm_metadata": {"model_info": info}},
    ],
)
def test_operator_ids_are_read_wherever_litellm_keeps_model_info(logger, writer, where):
    run(logger.async_pre_call_hook(UserAPIKeyAuth(), None, {"litellm_call_id": "call-1", "model": "r"}, "acompletion"))
    kwargs = {
        "litellm_call_id": "call-1",
        "call_type": "acompletion",
        "custom_llm_provider": "openai",
        "model": "gpt-4o-mini",
        "api_call_start_time": at(0),
        "litellm_params": where(dict(OPERATOR_INFO, id="deployment-9")),
    }
    logger.log_pre_api_call("gpt-4o-mini", [], kwargs)
    run(logger.async_log_success_event(dict(kwargs, standard_logging_object={}), None, T0, at(1)))
    [(_, attributes)] = writer.of(PROVIDER_ATTEMPT)
    assert attributes == OPERATOR_IDS
    assert logger._requests == {}
