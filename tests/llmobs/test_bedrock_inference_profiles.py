import threading

import pytest

from ddtrace.contrib.internal.botocore.services.bedrock import _resolve_application_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import _clear_inference_profile_cache
from ddtrace.llmobs._integrations._bedrock_inference_profiles import lookup_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import record_inference_profile


PROFILE_ARN = "arn:aws:bedrock:us-east-2:123456789012:application-inference-profile/p7aksl2pa6w7"
BASE_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_inference_profile_cache()
    yield
    _clear_inference_profile_cache()


def test_record_and_lookup_roundtrip():
    record_inference_profile(PROFILE_ARN, BASE_MODEL)
    assert lookup_inference_profile(PROFILE_ARN) == BASE_MODEL


def test_lookup_miss_returns_none():
    assert lookup_inference_profile(PROFILE_ARN) is None


def test_empty_args_are_noops():
    record_inference_profile("", BASE_MODEL)
    record_inference_profile(PROFILE_ARN, "")
    record_inference_profile("", "")
    assert lookup_inference_profile(PROFILE_ARN) is None
    assert lookup_inference_profile("") is None


def test_record_overwrites_existing_value():
    record_inference_profile(PROFILE_ARN, "older-model")
    record_inference_profile(PROFILE_ARN, BASE_MODEL)
    assert lookup_inference_profile(PROFILE_ARN) == BASE_MODEL


def test_concurrent_writes_do_not_lose_data():
    arns = [f"arn:aws:bedrock:us-east-2:111122223333:application-inference-profile/p{i:04d}" for i in range(50)]

    def writer(arn):
        record_inference_profile(arn, BASE_MODEL)

    threads = [threading.Thread(target=writer, args=(arn,)) for arn in arns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for arn in arns:
        assert lookup_inference_profile(arn) == BASE_MODEL


def test_resolve_application_inference_profile_cache_hit():
    record_inference_profile(PROFILE_ARN, BASE_MODEL)
    model_id, provider, name = _resolve_application_inference_profile(PROFILE_ARN, "custom", "p7aksl2pa6w7")
    assert model_id == BASE_MODEL
    assert provider == "anthropic"
    assert name == "claude-haiku-4-5-20251001-v1:0"


def test_resolve_application_inference_profile_cache_miss_returns_inputs():
    model_id, provider, name = _resolve_application_inference_profile(PROFILE_ARN, "custom", "p7aksl2pa6w7")
    assert model_id == PROFILE_ARN
    assert provider == "custom"
    assert name == "p7aksl2pa6w7"


def test_resolve_application_inference_profile_non_application_arn_passes_through():
    arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-tg1-large"
    model_id, provider, name = _resolve_application_inference_profile(arn, "amazon", "titan-tg1-large")
    assert model_id == arn
    assert provider == "amazon"
    assert name == "titan-tg1-large"


def test_resolve_application_inference_profile_non_string_input_passes_through():
    model_id, provider, name = _resolve_application_inference_profile(None, "custom", "foo")
    assert model_id is None
    assert provider == "custom"
    assert name == "foo"
