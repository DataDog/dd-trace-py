import pytest

from ddtrace.internal.openfeature._source import FeatureFlagSourceMode
from ddtrace.internal.openfeature._source import resolve_feature_flag_source_config


def test_source_mode_defaults_to_cdn_and_resolves_cdn_settings():
    config = resolve_feature_flag_source_config(
        {
            "DD_API_KEY": "test-api-key",
            "DD_FLAGGING_CDN_BASE_URL": "http://127.0.0.1:8123/mock/ufc/config",
            "DD_FLAGGING_CDN_POLL_INTERVAL_SECONDS": "3.5",
            "DD_FLAGGING_CDN_REQUEST_TIMEOUT_SECONDS": "1.25",
        }
    )

    assert config.mode is FeatureFlagSourceMode.CDN
    assert config.cdn.base_url == "http://127.0.0.1:8123/mock/ufc/config"
    assert config.cdn.api_key == "test-api-key"
    assert config.cdn.poll_interval_seconds == 3.5
    assert config.cdn.request_timeout_seconds == 1.25


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        ("remote_config", FeatureFlagSourceMode.REMOTE_CONFIG),
        ("REMOTE_CONFIG", FeatureFlagSourceMode.REMOTE_CONFIG),
        ("offline", FeatureFlagSourceMode.OFFLINE),
    ],
)
def test_source_mode_accepts_explicit_remote_config_and_reserved_offline(raw_mode, expected):
    config = resolve_feature_flag_source_config({"DD_FLAGGING_SOURCE_MODE": raw_mode})

    assert config.mode is expected


def test_invalid_source_mode_fails_before_network_work():
    with pytest.raises(ValueError, match="DD_FLAGGING_SOURCE_MODE"):
        resolve_feature_flag_source_config({"DD_FLAGGING_SOURCE_MODE": "agent"})
