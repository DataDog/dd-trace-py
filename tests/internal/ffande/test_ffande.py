"""Tests for FFAndE (Feature Flagging and Experimentation) product."""
import json

from ddtrace.internal.ffande._native import is_available
from ddtrace.internal.ffande._native import process_ffe_configuration
from ddtrace.internal.ffande.product import FFAndEAdapter
from ddtrace.internal.ffande.product import FFAndECapabilities
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload


def test_native_module_available():
    """Test that the native module is available after build."""
    assert is_available is True


def test_process_ffe_configuration_success():
    """Test successful FFE configuration processing."""
    config = {"rules": [{"flag": "test_flag", "enabled": True}]}
    config_bytes = json.dumps(config).encode("utf-8")

    result = process_ffe_configuration(config_bytes)
    assert result is True


def test_process_ffe_configuration_empty():
    """Test FFE configuration with empty bytes."""
    result = process_ffe_configuration(b"")
    assert result is False


def test_process_ffe_configuration_invalid_utf8():
    """Test FFE configuration with invalid UTF-8."""
    result = process_ffe_configuration(b"\xFF\xFE\xFD")
    assert result is False


def test_ffe_flag_configuration_rules_capability():
    """Test that FFE_FLAG_CONFIGURATION_RULES capability is bit 46."""
    assert FFAndECapabilities.FFE_FLAG_CONFIGURATION_RULES == (1 << 46)
    assert FFAndECapabilities.FFE_FLAG_CONFIGURATION_RULES == 70368744177664


def test_adapter_creation():
    """Test FFAndEAdapter instantiation."""
    adapter = FFAndEAdapter()
    assert adapter._subscriber._name == "FFE_FLAGS"


def test_rc_callback_with_valid_payload():
    """Test callback with valid payload."""
    adapter = FFAndEAdapter()

    metadata = ConfigMetadata(
        id="test-config-123",
        product_name="FFE_FLAGS",
        sha256_hash="abc123",
        length=100,
        tuf_version=1,
    )

    content = {
        "flags": {
            "removable-flag": {
                "key": "removable-flag",
                "enabled": True,
                "variationType": "BOOLEAN",
                "defaultVariation": "true",
                "variations": {
                    "true": {"key": "true", "value": True},
                    "false": {"key": "false", "value": False},
                },
                "allocations": [
                    {
                        "key": "remove-allocation",
                        "percentages": {"true": 100, "false": 0},
                        "filters": [],
                    }
                ],
            }
        }
    }

    payload = Payload(metadata=metadata, path="datadog/1/FFE_FLAGS/test/config.json", content=content)

    # Should not raise
    adapter.rc_callback([payload])


def test_rc_callback_with_deletion():
    """Test callback with deletion (None content)."""
    adapter = FFAndEAdapter()

    metadata = ConfigMetadata(
        id="test-config-456",
        product_name="FFE_FLAGS",
        sha256_hash="def456",
        length=0,
        tuf_version=1,
    )

    payload = Payload(metadata=metadata, path="datadog/1/FFE_FLAGS/test/config.json", content=None)

    # Should not raise
    adapter.rc_callback([payload])


def test_rc_callback_with_no_metadata():
    """Test callback with payload missing metadata."""
    adapter = FFAndEAdapter()

    payload = Payload(metadata=None, path="datadog/1/FFE_FLAGS/test/config.json", content={"test": True})

    # Should handle gracefully
    adapter.rc_callback([payload])


def test_rc_callback_with_complex_config():
    """Test callback with complex configuration."""
    adapter = FFAndEAdapter()

    metadata = ConfigMetadata(
        id="test-config-789",
        product_name="FFE_FLAGS",
        sha256_hash="ghi789",
        length=500,
        tuf_version=2,
    )

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

    payload = Payload(metadata=metadata, path="datadog/1/FFE_FLAGS/test/config.json", content=content)

    # Should process without errors
    adapter.rc_callback([payload])
