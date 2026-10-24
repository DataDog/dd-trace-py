import pytest

from ddtrace.contrib.internal.langchain.patch import _extract_model_name
from ddtrace.llmobs._integrations._bedrock_inference_profiles import _clear_inference_profile_cache
from ddtrace.llmobs._integrations._bedrock_inference_profiles import lookup_inference_profile
from tests.contrib.patch_scenarios import PATCH_SCENARIOS


class _FakeInstance:
    """Minimal stand-in for a langchain LLM/chat-model instance."""

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


@pytest.fixture(
    params=[
        pytest.param(
            (
                {"model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
            ),
            id="model_id",
        ),
        pytest.param(
            (
                {
                    "model_id": "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile",
                    "base_model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                },
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
            ),
            id="base_model_id",
        ),
        pytest.param(
            (
                {"base_model_id": None, "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
            ),
            id="base_model_id_none",
        ),
        pytest.param(
            (
                {"base_model_id": "", "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
            ),
            id="base_model_id_empty",
        ),
        pytest.param(({"model": "models/gemini-2.5-flash"}, "gemini-2.5-flash"), id="path_prefix"),
        pytest.param(({}, None), id="no_model_attrs"),
    ]
)
def extract_model_name_case(request):
    attrs, expected_model_name = request.param
    return _FakeInstance(**attrs), expected_model_name


def test_extract_model_name(extract_model_name_case):
    instance, expected_model_name = extract_model_name_case
    assert _extract_model_name(instance) == expected_model_name


@pytest.fixture
def clear_inference_profile_cache():
    _clear_inference_profile_cache()
    yield
    _clear_inference_profile_cache()


def test_extract_model_name_records_inference_profile_mapping(clear_inference_profile_cache):
    arn = "arn:aws:bedrock:us-east-2:123456789012:application-inference-profile/p7aksl2pa6w7"
    base = "anthropic.claude-haiku-4-5-20251001-v1:0"
    instance = _FakeInstance(model_id=arn, base_model_id=base)

    _extract_model_name(instance)

    assert lookup_inference_profile(arn) == base


def test_extract_model_name_without_base_model_id_does_not_record(clear_inference_profile_cache):
    arn = "arn:aws:bedrock:us-east-2:123456789012:application-inference-profile/p7aksl2pa6w7"
    instance = _FakeInstance(model_id=arn)

    _extract_model_name(instance)

    assert lookup_inference_profile(arn) is None


def test_extract_model_name_non_application_arn_does_not_record(clear_inference_profile_cache):
    arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-tg1-large"
    instance = _FakeInstance(model_id=arn, base_model_id="amazon.titan-tg1-large")

    _extract_model_name(instance)

    assert lookup_inference_profile(arn) is None


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestLangchainPatch(python_process, scenario):
    module = "tests.contrib.langchain"
    module += "._test_langchain_patch_scenarios"
    case = "TestLangchainPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
