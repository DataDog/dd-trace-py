import pytest

from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._remoteconfiguration import FeatureFlagCallback
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


def _payload(path, content):
    metadata = ConfigMetadata(
        id=path.rsplit("/", 2)[-2],
        product_name="FFE_FLAGS",
        sha256_hash=None,
        length=None,
        tuf_version=1,
    )
    return Payload(metadata=metadata, path=path, content=content)


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
    callback = FeatureFlagCallback()
    callback([payload])


_OLD_PATH = "datadog/2/FFE_FLAGS/old-config/config"
_NEW_PATH = "datadog/2/FFE_FLAGS/new-config/config"


def test_deletion_of_replaced_config_keeps_the_current_one():
    """Regression: an add and the removal it replaces may arrive in separate batches.

    A forked child reads configuration from shared memory, which publishes one
    manifest per file operation, so replacing a config can reach the child as
    add(new) followed by remove(old) instead of the removals-first batch the origin
    dispatches. Clearing on the late removal would drop a live configuration and make
    every later evaluation report PROVIDER_NOT_READY.
    """
    callback = FeatureFlagCallback()

    callback([_payload(_NEW_PATH, create_config(create_boolean_flag("my-flag")))])
    assert _get_ffe_config() is not None

    callback([_payload(_OLD_PATH, None)])

    assert _get_ffe_config() is not None


def test_deletion_of_applied_config_clears_it():
    """The removal that does name the applied config still clears it."""
    callback = FeatureFlagCallback()

    callback([_payload(_NEW_PATH, create_config(create_boolean_flag("my-flag")))])
    callback([_payload(_NEW_PATH, None)])

    assert _get_ffe_config() is None


def test_removals_first_batch_keeps_the_new_config():
    """The origin's own ordering (removals first, then the add) is unaffected."""
    callback = FeatureFlagCallback()

    callback([_payload(_OLD_PATH, create_config(create_boolean_flag("old-flag")))])
    callback(
        [
            _payload(_OLD_PATH, None),
            _payload(_NEW_PATH, create_config(create_boolean_flag("new-flag"))),
        ]
    )

    assert _get_ffe_config() is not None
    # A later removal of the config that was replaced must not clear the new one.
    callback([_payload(_OLD_PATH, None)])
    assert _get_ffe_config() is not None
