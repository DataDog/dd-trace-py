from ddtrace.internal.openfeature._remoteconfiguration import featureflag_rc_callback
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload


def test_rc_callback_with_deletion():
    """Test callback with deletion (None content)."""
    metadata = ConfigMetadata(
        id="test-config-456",
        product_name="FFE_FLAGS",
        sha256_hash="def456",
        length=0,
        tuf_version=1,
    )

    payload = Payload(metadata=metadata, path="datadog/1/FFE_FLAGS/test/config.json", content=None)

    # Should not raise
    featureflag_rc_callback([payload])


def test_rc_callback_with_no_metadata():
    """Test callback with payload missing metadata."""

    payload = Payload(metadata=None, path="datadog/1/FFE_FLAGS/test/config.json", content={"test": True})

    # Should handle gracefully
    featureflag_rc_callback([payload])


def test_rc_callback_with_complex_config():
    """Test callback with complex configuration."""
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
    featureflag_rc_callback([payload])
