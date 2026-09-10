import pytest

from ddtrace.internal.openfeature._source_selection import AGENTLESS
from ddtrace.internal.openfeature._source_selection import DISABLED
from ddtrace.internal.openfeature._source_selection import REMOTE_CONFIG
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.openfeature._source_selection import resolve_configuration_source
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from tests.utils import override_global_config


def _config(**env):
    """Build an OpenFeatureConfig where the given env vars are marked as provided."""
    return OpenFeatureConfig(source=env)


# ---------------------------------------------------------------------------
# Source resolution matrix (mirrors the system-tests contract)
# ---------------------------------------------------------------------------


def test_default_is_agentless():
    assert resolve_configuration_source(_config()) == AGENTLESS


def test_explicit_agentless():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="agentless")) == AGENTLESS


def test_explicit_remote_config():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="remote_config")) == REMOTE_CONFIG


def test_source_is_case_and_whitespace_insensitive():
    assert (
        resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="  Remote_Config  "))
        == REMOTE_CONFIG
    )


def test_invalid_source_fails_closed():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="invalid")) == DISABLED


def test_reserved_offline_source_fails_closed():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="offline")) == DISABLED


def test_blank_source_is_treated_as_absent():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="   ")) == AGENTLESS


def test_kill_switch_disables():
    assert resolve_configuration_source(_config(DD_FEATURE_FLAGS_ENABLED="false")) == DISABLED


def test_kill_switch_overrides_legacy():
    cfg = _config(DD_FEATURE_FLAGS_ENABLED="false", DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED="true")
    assert resolve_configuration_source(cfg) == DISABLED


def test_grandfather_legacy_true_selects_remote_config():
    cfg = _config(DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED="true")
    assert resolve_configuration_source(cfg) == REMOTE_CONFIG


def test_grandfather_legacy_false_disables():
    cfg = _config(DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED="false")
    assert resolve_configuration_source(cfg) == DISABLED


def test_explicit_agentless_wins_over_legacy_true():
    cfg = _config(
        DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="agentless",
        DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED="true",
    )
    assert resolve_configuration_source(cfg) == AGENTLESS


def test_explicit_remote_config_wins_over_legacy_false():
    cfg = _config(
        DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="remote_config",
        DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED="false",
    )
    assert resolve_configuration_source(cfg) == REMOTE_CONFIG


# ---------------------------------------------------------------------------
# Agentless factory
# ---------------------------------------------------------------------------


def test_create_returns_none_when_not_agentless():
    cfg = _config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="remote_config")
    assert create_agentless_source(cfg, lambda _: None) is None


def test_create_default_endpoint_requires_api_key():
    cfg = _config()
    with override_global_config({"_dd_api_key": None}):
        assert create_agentless_source(cfg, lambda _: None) is None


def test_create_default_endpoint_with_api_key():
    cfg = _config()
    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com"}):
        src = create_agentless_source(cfg, lambda _: None)
    assert src is not None
    assert src._api_key == "secret"
    assert src._conn_url == "https://ufc-server.ff-cdn.datadoghq.com/"


def test_create_custom_endpoint_omits_api_key():
    cfg = _config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL="http://host.docker.internal:8126")
    with override_global_config({"_dd_api_key": "secret"}):
        src = create_agentless_source(cfg, lambda _: None)
    assert src is not None
    assert src._api_key is None  # custom endpoint: key omitted even though one is set


def test_create_custom_endpoint_starts_without_api_key():
    cfg = _config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL="http://host.docker.internal:8126")
    with override_global_config({"_dd_api_key": None}):
        src = create_agentless_source(cfg, lambda _: None)
    assert src is not None  # missing key does not block a custom endpoint


@pytest.mark.parametrize("bad_url", ["ftp://flags.example.test", "https://flags.example.test path"])
def test_create_invalid_endpoint_returns_none(bad_url):
    cfg = _config(DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL=bad_url)
    with override_global_config({"_dd_api_key": "secret"}):
        assert create_agentless_source(cfg, lambda _: None) is None


# ---------------------------------------------------------------------------
# Numeric settings degrade instead of breaking the import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "attribute", "expected"),
    [
        (
            "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_POLL_INTERVAL_SECONDS",
            "configuration_source_agentless_poll_interval_seconds",
            30,
        ),
        (
            "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_REQUEST_TIMEOUT_SECONDS",
            "configuration_source_agentless_request_timeout_seconds",
            5,
        ),
        (
            "DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS",
            "initialization_timeout_ms",
            10000,
        ),
    ],
)
def test_unparsable_integer_setting_falls_back_to_default(monkeypatch, env_name, attribute, expected):
    # OpenFeatureConfig is built at module scope, so raising here would surface as an
    # ImportError for ddtrace.openfeature and take the application down at startup.
    monkeypatch.setenv(env_name, "0.2")
    assert getattr(OpenFeatureConfig(), attribute) == expected


def test_unparsable_float_setting_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DD_FFE_INTAKE_HEARTBEAT_INTERVAL", "not-a-number")
    assert OpenFeatureConfig().ffe_intake_heartbeat_interval == 1.0
