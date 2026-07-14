import threading
from unittest import mock

import botocore.exceptions
import pytest

from ddtrace.contrib.internal.botocore import patch as _botocore_patch  # noqa: F401  (registers config.botocore)
from ddtrace.contrib.internal.botocore.services.bedrock import _foundation_model_id_from_profile
from ddtrace.contrib.internal.botocore.services.bedrock import _region_from_arn
from ddtrace.contrib.internal.botocore.services.bedrock import _resolve_application_inference_profile
from ddtrace.llmobs._integrations import _bedrock_inference_profiles as _profiles
from ddtrace.llmobs._integrations._bedrock_inference_profiles import _clear_inference_profile_cache
from ddtrace.llmobs._integrations._bedrock_inference_profiles import end_resolve
from ddtrace.llmobs._integrations._bedrock_inference_profiles import is_resolve_in_backoff
from ddtrace.llmobs._integrations._bedrock_inference_profiles import lookup_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import record_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import try_begin_resolve
from tests.utils import override_config


PROFILE_ARN = "arn:aws:bedrock:us-east-2:123456789012:application-inference-profile/p7aksl2pa6w7"
BASE_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
# The modelArn as GetInferenceProfile returns it for an application profile over Claude.
PROFILE_RESPONSE = {
    "models": [
        {"modelArn": "arn:aws:bedrock:::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"},
        {"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"},
    ]
}


class _FakeFrozenCredentials:
    access_key = "AKIAEXAMPLE"
    secret_key = "secret"
    token = "token"


class _FakeCredentials:
    def get_frozen_credentials(self):
        return _FakeFrozenCredentials()


class _FakeRuntimeClient:
    """Stand-in for the user's bedrock-runtime botocore client."""

    def __init__(self, credentials=None):
        self._credentials = _FakeCredentials() if credentials is None else credentials
        self.meta = mock.Mock(region_name="us-east-1")
        self._client_config = None

    def _get_credentials(self):
        return self._credentials


def _control_plane_session(response=None, error=None, has_method=True):
    """Return a mock botocore session whose client.get_inference_profile behaves as configured."""
    control_client = mock.Mock(spec=["get_inference_profile"] if has_method else [])

    def _get_inference_profile(inferenceProfileIdentifier=None):
        if error is not None:
            raise error
        return response

    if has_method:
        control_client.get_inference_profile.side_effect = _get_inference_profile
    session = mock.Mock()
    session.create_client.return_value = control_client
    return session, control_client


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_inference_profile_cache()
    yield
    _clear_inference_profile_cache()


@pytest.fixture
def resolve_enabled():
    with override_config("botocore", {"bedrock_resolve_inference_profile": True}):
        yield


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


@pytest.mark.parametrize(
    "model_id, provider, name",
    [
        (PROFILE_ARN, "custom", "p7aksl2pa6w7"),  # application-inference-profile ARN, cache miss, flag off
        ("arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-tg1-large", "amazon", "titan-tg1-large"),
        (None, "custom", "foo"),  # non-string input
    ],
    ids=["cache-miss", "non-application-arn", "non-string"],
)
def test_resolve_passes_through_inputs_unchanged(model_id, provider, name):
    assert _resolve_application_inference_profile(model_id, provider, name) == (model_id, provider, name)


def test_region_from_arn():
    assert _region_from_arn(PROFILE_ARN) == "us-east-2"
    assert _region_from_arn("arn:aws:bedrock:::foundation-model/x") is None
    assert _region_from_arn("not-an-arn") is None


def test_foundation_model_id_from_profile():
    assert _foundation_model_id_from_profile(PROFILE_RESPONSE) == BASE_MODEL
    assert _foundation_model_id_from_profile({"models": []}) is None
    assert _foundation_model_id_from_profile({}) is None
    # A cross-region inference-profile ARN (no bare foundation model) is not usable for costing.
    assert _foundation_model_id_from_profile({"models": [{"modelArn": "arn:...:inference-profile/us.x"}]}) is None


def test_resolve_via_get_inference_profile_populates_cache(resolve_enabled):
    session, _ = _control_plane_session(response=PROFILE_RESPONSE)
    instance = _FakeRuntimeClient()
    with mock.patch("botocore.session.get_session", return_value=session):
        model_id, provider, name = _resolve_application_inference_profile(
            PROFILE_ARN, "custom", "p7aksl2pa6w7", instance
        )
    assert model_id == BASE_MODEL
    assert provider == "anthropic"
    assert name == "claude-haiku-4-5-20251001-v1:0"
    # cached for next time, and the control-plane client was built for the profile's region
    assert lookup_inference_profile(PROFILE_ARN) == BASE_MODEL
    session.create_client.assert_called_once()
    assert session.create_client.call_args.kwargs["region_name"] == "us-east-2"


def test_no_resolution_call_when_flag_disabled():
    instance = _FakeRuntimeClient()
    with mock.patch("botocore.session.get_session") as get_session:
        model_id, _, _ = _resolve_application_inference_profile(PROFILE_ARN, "custom", "p7aksl2pa6w7", instance)
    get_session.assert_not_called()
    assert model_id == PROFILE_ARN


def test_cache_hit_does_not_construct_client(resolve_enabled):
    record_inference_profile(PROFILE_ARN, BASE_MODEL)
    instance = _FakeRuntimeClient()
    with mock.patch("botocore.session.get_session") as get_session:
        model_id, _, _ = _resolve_application_inference_profile(PROFILE_ARN, "custom", "p7aksl2pa6w7", instance)
    get_session.assert_not_called()
    assert model_id == BASE_MODEL


def _instance_without_credentials():
    instance = _FakeRuntimeClient()
    instance._credentials = None  # _get_credentials() returns None -> no usable creds
    return instance


@pytest.mark.parametrize(
    "make_session, make_instance",
    [
        # GetInferenceProfile returned no underlying foundation model
        (lambda: _control_plane_session(response={"models": [{}]})[0], _FakeRuntimeClient),
        # botocore too old: the control-plane client has no get_inference_profile
        (lambda: _control_plane_session(has_method=False)[0], _FakeRuntimeClient),
        # the runtime client exposes no usable credentials
        (lambda: mock.Mock(), _instance_without_credentials),
    ],
    ids=["no-foundation-model", "old-botocore", "no-credentials"],
)
def test_failure_cause_backs_off(resolve_enabled, make_session, make_instance):
    with mock.patch("botocore.session.get_session", return_value=make_session()):
        model_id, provider, _ = _resolve_application_inference_profile(
            PROFILE_ARN, "custom", "p7aksl2pa6w7", make_instance()
        )
    assert model_id == PROFILE_ARN
    assert provider == "custom"
    assert is_resolve_in_backoff(PROFILE_ARN)


def test_failure_backs_off_and_is_not_retried_within_window(resolve_enabled):
    error = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetInferenceProfile"
    )
    session, _ = _control_plane_session(error=error)
    instance = _FakeRuntimeClient()
    with mock.patch("botocore.session.get_session", return_value=session) as get_session:
        assert _resolve_application_inference_profile(PROFILE_ARN, "custom", "x", instance)[0] == PROFILE_ARN
        assert is_resolve_in_backoff(PROFILE_ARN)
        # a second call within the backoff window makes no additional AWS call
        _resolve_application_inference_profile(PROFILE_ARN, "custom", "x", instance)
    get_session.assert_called_once()


def test_backoff_expires_and_recovers_after_fix(resolve_enabled):
    # First attempt fails; the ARN backs off. Once the window elapses (e.g. AWS recovered or
    # the IAM permission was fixed) the next call retries and succeeds -- no restart needed.
    error = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetInferenceProfile"
    )
    control_client = mock.Mock(spec=["get_inference_profile"])
    control_client.get_inference_profile.side_effect = [error, PROFILE_RESPONSE]
    session = mock.Mock()
    session.create_client.return_value = control_client
    instance = _FakeRuntimeClient()
    clock = {"t": 1000.0}
    with mock.patch.object(_profiles.time, "monotonic", side_effect=lambda: clock["t"]):
        with mock.patch("botocore.session.get_session", return_value=session):
            assert _resolve_application_inference_profile(PROFILE_ARN, "custom", "x", instance)[0] == PROFILE_ARN
            assert is_resolve_in_backoff(PROFILE_ARN)
            # within the window: skipped, no extra call
            _resolve_application_inference_profile(PROFILE_ARN, "custom", "x", instance)
            assert control_client.get_inference_profile.call_count == 1
            # advance past the largest possible backoff window, then retry succeeds
            clock["t"] += _profiles._BACKOFF_MAX_SECONDS + 1
            model_id, provider, _ = _resolve_application_inference_profile(PROFILE_ARN, "custom", "x", instance)
    assert control_client.get_inference_profile.call_count == 2
    assert model_id == BASE_MODEL
    assert provider == "anthropic"
    assert lookup_inference_profile(PROFILE_ARN) == BASE_MODEL


def test_single_flight_claim_prevents_concurrent_resolution():
    # While one caller holds the claim, another is refused; releasing frees it.
    assert try_begin_resolve(PROFILE_ARN) is True
    assert try_begin_resolve(PROFILE_ARN) is False
    end_resolve(PROFILE_ARN)
    assert try_begin_resolve(PROFILE_ARN) is True
    end_resolve(PROFILE_ARN)


def test_canary_botocore_credential_path_still_exists():
    """If a botocore upgrade moves the private credential path, this fails loudly so the
    resolver doesn't silently degrade to a no-op.
    """
    import botocore.session

    client = botocore.session.get_session().create_client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="secret",
    )
    creds = client._get_credentials() or client._request_signer._credentials
    assert creds is not None
    frozen = creds.get_frozen_credentials()
    assert frozen.access_key == "AKIAEXAMPLE"
