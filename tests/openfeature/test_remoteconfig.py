from unittest.mock import patch

from ddtrace.internal.openfeature._remoteconfiguration import FeatureFlagCallback
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload


def _make_metadata(config_id="test-config"):
    return ConfigMetadata(
        id=config_id,
        product_name="FFE_FLAGS",
        sha256_hash="abc123",
        length=100,
        tuf_version=1,
    )


def _make_config(flags_dict):
    return {"flags": flags_dict, "format": "SERVER"}


def _make_payload(path, content, config_id="test-config"):
    return Payload(metadata=_make_metadata(config_id), path=path, content=content)


def test_rc_callback_with_deletion():
    """Test callback with deletion (None content)."""
    payload = _make_payload("datadog/1/FFE_FLAGS/test/config.json", None, "test-config-456")

    # Should not raise
    callback = FeatureFlagCallback()
    callback([payload])


def test_rc_callback_with_no_metadata():
    """Test callback with payload missing metadata."""

    payload = Payload(metadata=None, path="datadog/1/FFE_FLAGS/test/config.json", content={"test": True})

    # Should handle gracefully
    callback = FeatureFlagCallback()
    callback([payload])


def test_rc_callback_with_complex_config():
    """Test callback with complex configuration."""
    content = {
        "testBooleanAndStringFlags": {
            "flags": {
                "test-boolean-flag": {
                    "key": "test-boolean-flag",
                    "enabled": True,
                    "variationType": "BOOLEAN",
                    "defaultVariation": "true",
                    "variations": {
                        "true": {"key": "true", "value": True},
                        "false": {"key": "false", "value": False},
                    },
                    "allocations": [
                        {
                            "key": "allocation-1",
                            "rules": [],
                            "splits": [
                                {"variationKey": "true", "percentage": 100},
                                {"variationKey": "false", "percentage": 0},
                            ],
                        }
                    ],
                },
                "test-string-flag": {
                    "key": "test-string-flag",
                    "enabled": True,
                    "variationType": "STRING",
                    "defaultVariation": "variant-a",
                    "variations": {
                        "variant-a": {"key": "variant-a", "value": "value-a"},
                        "variant-b": {"key": "variant-b", "value": "value-b"},
                    },
                    "allocations": [
                        {
                            "key": "allocation-2",
                            "rules": [],
                            "shardedSplits": [
                                {
                                    "variationKey": "variant-a",
                                    "shards": [{"salt": "test-salt", "ranges": [{"start": 0, "end": 5000}]}],
                                },
                                {
                                    "variationKey": "variant-b",
                                    "shards": [{"salt": "test-salt", "ranges": [{"start": 5000, "end": 10000}]}],
                                },
                            ],
                            "totalShards": 10000,
                        }
                    ],
                },
            }
        }
    }

    payload = _make_payload("datadog/1/FFE_FLAGS/test/config.json", content, "test-config-789")

    # Should process without errors
    callback = FeatureFlagCallback()
    callback([payload])


@patch("ddtrace.internal.openfeature._remoteconfiguration.process_ffe_configuration")
def test_two_configs_single_callback(mock_process):
    """Two configs in a single callback - both flags accessible after merge."""
    callback = FeatureFlagCallback()

    config_a = _make_config({"flag-a": {"key": "flag-a", "enabled": True}})
    config_b = _make_config({"flag-b": {"key": "flag-b", "enabled": True}})

    callback(
        [
            _make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a, "config-a"),
            _make_payload("datadog/1/FFE_FLAGS/env-llmobs/config.json", config_b, "config-b"),
        ]
    )

    mock_process.assert_called_once()
    merged = mock_process.call_args[0][0]
    assert "flag-a" in merged["flags"]
    assert "flag-b" in merged["flags"]


@patch("ddtrace.internal.openfeature._remoteconfiguration.process_ffe_configuration")
def test_delta_update_separate_callbacks(mock_process):
    """Delta update: first config, then second config in separate callbacks - both flags accessible."""
    callback = FeatureFlagCallback()

    config_a = _make_config({"flag-a": {"key": "flag-a", "enabled": True}})
    callback([_make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a, "config-a")])
    assert mock_process.call_count == 1

    config_b = _make_config({"flag-b": {"key": "flag-b", "enabled": True}})
    callback([_make_payload("datadog/1/FFE_FLAGS/env-llmobs/config.json", config_b, "config-b")])
    assert mock_process.call_count == 2

    merged = mock_process.call_args[0][0]
    assert "flag-a" in merged["flags"]
    assert "flag-b" in merged["flags"]


@patch("ddtrace.internal.openfeature._remoteconfiguration._set_ffe_config")
@patch("ddtrace.internal.openfeature._remoteconfiguration.process_ffe_configuration")
def test_delete_one_config_other_survives(mock_process, mock_set_config):
    """Deletion of one config: other config's flags survive."""
    callback = FeatureFlagCallback()

    config_a = _make_config({"flag-a": {"key": "flag-a", "enabled": True}})
    config_b = _make_config({"flag-b": {"key": "flag-b", "enabled": True}})
    callback(
        [
            _make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a, "config-a"),
            _make_payload("datadog/1/FFE_FLAGS/env-llmobs/config.json", config_b, "config-b"),
        ]
    )

    # Delete config_a
    callback([_make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", None, "config-a")])

    merged = mock_process.call_args[0][0]
    assert "flag-a" not in merged["flags"]
    assert "flag-b" in merged["flags"]
    mock_set_config.assert_not_called()


@patch("ddtrace.internal.openfeature._remoteconfiguration.process_ffe_configuration")
def test_update_one_config(mock_process):
    """Update one config: flags from both configs reflect the update."""
    callback = FeatureFlagCallback()

    config_a = _make_config({"flag-a": {"key": "flag-a", "enabled": True}})
    config_b = _make_config({"flag-b": {"key": "flag-b", "enabled": True}})
    callback(
        [
            _make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a, "config-a"),
            _make_payload("datadog/1/FFE_FLAGS/env-llmobs/config.json", config_b, "config-b"),
        ]
    )

    # Update config_a with a new flag value
    config_a_updated = _make_config({"flag-a": {"key": "flag-a", "enabled": False}})
    callback([_make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a_updated, "config-a")])

    merged = mock_process.call_args[0][0]
    assert merged["flags"]["flag-a"]["enabled"] is False
    assert merged["flags"]["flag-b"]["enabled"] is True


@patch("ddtrace.internal.openfeature._remoteconfiguration._set_ffe_config")
@patch("ddtrace.internal.openfeature._remoteconfiguration.process_ffe_configuration")
def test_delete_all_configs(mock_process, mock_set_config):
    """Delete all configs: _set_ffe_config(None) is called."""
    callback = FeatureFlagCallback()

    config_a = _make_config({"flag-a": {"key": "flag-a", "enabled": True}})
    callback([_make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", config_a, "config-a")])

    # Delete it
    callback([_make_payload("datadog/1/FFE_FLAGS/env-prod/config.json", None, "config-a")])

    mock_set_config.assert_called_once_with(None)
