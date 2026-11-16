import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.internal.openfeature import _remoteconfiguration
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._remoteconfiguration import FeatureFlagCallback
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_integer_flag
from tests.openfeature.config_helpers import create_string_flag


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.mark.parametrize("has_rc_capability", [False, True])
@pytest.mark.parametrize("evaluator_capabilities", [(), ("unknown-capability",), ("dependent-flag-variant-key-v1",)])
def test_enable_rc_advertises_only_supported_evaluator_capabilities(has_rc_capability, evaluator_capabilities):
    rules = object()
    dependent_flags = object()
    native_capabilities = SimpleNamespace(FfeFlagConfigurationRules=rules)
    if has_rc_capability:
        native_capabilities.FfeFlagDependentFlagVariantKeyV1 = dependent_flags

    with (
        mock.patch.object(_remoteconfiguration, "RemoteConfigCapabilities", native_capabilities),
        mock.patch.object(_remoteconfiguration, "get_evaluator_capabilities", return_value=evaluator_capabilities),
        mock.patch.object(_remoteconfiguration, "remoteconfig_poller") as poller,
    ):
        _remoteconfiguration.enable_featureflags_rc()

    expected = [rules]
    if has_rc_capability and "dependent-flag-variant-key-v1" in evaluator_capabilities:
        expected.append(dependent_flags)
    poller.register_callback.assert_called_once_with(
        _remoteconfiguration.FFE_FLAGS_PRODUCT,
        _remoteconfiguration._featureflag_rc_callback,
        capabilities=expected,
    )
    poller.enable_product.assert_called_once_with(_remoteconfiguration.FFE_FLAGS_PRODUCT)


def test_current_evaluator_does_not_advertise_dependent_flags():
    with mock.patch.object(_remoteconfiguration, "remoteconfig_poller") as poller:
        _remoteconfiguration.enable_featureflags_rc()

    assert poller.register_callback.call_args.kwargs["capabilities"] == [
        _remoteconfiguration.RemoteConfigCapabilities.FfeFlagConfigurationRules
    ]


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


def test_debug_logs_applied_and_removed_configuration(caplog):
    callback = FeatureFlagCallback()

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([_payload(_NEW_PATH, create_config(create_boolean_flag("a"), create_boolean_flag("b")))])
        callback([_payload(_NEW_PATH, None)])

    assert (
        "Feature Flags: remote_config configuration apply applied successfully (2 flag(s), config_id=new-config, "
        "version=1, configuration_env=test, created_at=2025-10-31T00:00:00Z)"
    ) in caplog.messages
    assert (
        "Feature Flags: remote_config configuration removed; evaluations return default values until configuration "
        "is applied again"
    ) in caplog.messages


def test_debug_does_not_report_rejected_configuration_as_applied(caplog):
    callback = FeatureFlagCallback()

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([_payload(_NEW_PATH, {"flags": {"broken": {}}})])

    assert not any("applied successfully" in m for m in caplog.messages)
    assert (
        "Feature Flags: remote_config configuration apply rejected (config_id=new-config); no configuration is active"
    ) in caplog.messages


def test_debug_rejected_update_reports_the_previous_configuration_as_active(caplog):
    callback = FeatureFlagCallback()
    callback([_payload(_NEW_PATH, create_config(create_boolean_flag("a")))])

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([_payload(_NEW_PATH, {"flags": {"broken": {}}})])

    assert (
        "Feature Flags: remote_config configuration modify rejected (config_id=new-config); the previously applied "
        "configuration remains active"
    ) in caplog.messages
    assert _get_ffe_config() is not None


def test_debug_ignored_deletion_is_not_reported_as_removed(caplog):
    callback = FeatureFlagCallback()
    callback([_payload(_NEW_PATH, create_config(create_boolean_flag("my-flag")))])

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([_payload(_OLD_PATH, None)])

    assert not any(m.startswith("Feature Flags: remote_config configuration removed") for m in caplog.messages)


def test_debug_logs_update_of_applied_configuration_as_modify(caplog):
    callback = FeatureFlagCallback()
    callback([_payload(_NEW_PATH, create_config(create_boolean_flag("a")))])

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([_payload(_NEW_PATH, create_config(create_boolean_flag("a"), create_boolean_flag("b")))])
        callback([_payload(_OLD_PATH, create_config(create_boolean_flag("c")))])

    applied = [m for m in caplog.messages if "applied successfully" in m]
    identity = "version=1, configuration_env=test, created_at=2025-10-31T00:00:00Z"
    assert applied == [
        f"Feature Flags: remote_config configuration modify applied successfully (2 flag(s), config_id=new-config, "
        f"{identity})",
        f"Feature Flags: remote_config configuration apply applied successfully (1 flag(s), config_id=old-config, "
        f"{identity})",
    ], applied


def test_debug_logs_rejected_flags_inside_an_applied_configuration(caplog):
    broken = create_boolean_flag("broken")
    broken["variations"] = {"true": {"key": "true", "value": "not-a-boolean"}}
    broken_string = create_string_flag("broken-string", "ok")
    broken_string["variations"] = {"ok": {"key": "ok", "value": 123}}
    disabled = create_boolean_flag("off", enabled=False)
    config = create_config(create_boolean_flag("ok"), broken, broken_string, disabled)

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._native"):
        FeatureFlagCallback()([_payload(_NEW_PATH, config)])

    assert _get_ffe_config() is not None
    assert (
        "Feature Flags: configuration parsed: 4 flag(s), 2 usable, 2 rejected "
        "(invalid flag configuration: broken: configuration error, broken-string: configuration error)"
    ) in caplog.messages


def test_debug_flag_summary_does_not_report_valid_flags_of_any_type_as_rejected(caplog):
    config = create_config(
        create_string_flag("string", "hello"),
        create_integer_flag("integer", 7),
        create_string_flag("disabled-string", "off", enabled=False),
    )

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._native"):
        FeatureFlagCallback()([_payload(_NEW_PATH, config)])

    assert "Feature Flags: configuration parsed: 3 flag(s), 3 usable, 0 rejected" in caplog.messages
    assert _get_ffe_snapshot().identity == {
        "created_at": config["createdAt"],
        "environment": config["environment"],
    }


def test_debug_logs_empty_configuration_as_zero_flags(caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._native"):
        FeatureFlagCallback()([_payload(_NEW_PATH, create_config())])

    assert "Feature Flags: configuration parsed: 0 flag(s), 0 usable, 0 rejected" in caplog.messages


def test_flag_summary_is_skipped_when_debug_disabled(caplog):
    with caplog.at_level(logging.WARNING, logger="ddtrace.internal.openfeature._native"):
        with mock.patch("ddtrace.internal.openfeature._native._log_flag_summary") as log_flag_summary:
            FeatureFlagCallback()([_payload(_NEW_PATH, create_config(create_boolean_flag("a")))])

    assert _get_ffe_config() is not None
    log_flag_summary.assert_not_called()
    assert _get_ffe_snapshot().identity is None


def test_flag_summary_failure_does_not_block_applying_the_configuration(caplog):
    class BrokenTypes:
        @property
        def Boolean(self):
            raise RuntimeError("summary failed")

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._native"):
        with mock.patch("ddtrace.internal.openfeature._native.VariationType", BrokenTypes()):
            FeatureFlagCallback()([_payload(_NEW_PATH, create_config(create_boolean_flag("a")))])

    assert _get_ffe_config() is not None
    assert "Feature Flags: could not check parsed flags for rejections" in caplog.messages


def test_debug_logs_configuration_restored_after_removal(caplog):
    callback = FeatureFlagCallback()
    payload = _payload(_NEW_PATH, create_config(create_boolean_flag("flag")))
    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._remoteconfiguration"):
        callback([payload])
        callback([_payload(_NEW_PATH, None)])
        callback([payload])
    assert caplog.messages.count("Feature Flags: remote_config configuration recovered after removal") == 1


def test_debug_logs_all_rejected_flags_without_their_definitions(caplog):
    broken = create_boolean_flag("broken")
    broken["variations"] = {"true": {"key": "true", "value": "invalid-boolean"}}
    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._native"):
        FeatureFlagCallback()([_payload(_NEW_PATH, create_config(broken))])
    assert (
        "Feature Flags: configuration parsed: 1 flag(s), 0 usable, 1 rejected "
        "(invalid flag configuration: broken: configuration error)" in caplog.messages
    )
    # The definition holds variation values and targeting rules, which may be sensitive.
    assert "invalid-boolean" not in caplog.text
    assert "native evaluator does not expose field-level rejection details" in caplog.text


def test_shared_remote_config_logs_retry_and_recovery(caplog):
    poller = RemoteConfigPoller()
    with (
        caplog.at_level(logging.DEBUG, logger="ddtrace.internal.remoteconfig.worker"),
        mock.patch.object(poller._client, "request", side_effect=[False, True]),
        mock.patch.object(poller._client, "refresh_interval", return_value=0.5),
    ):
        poller._online()
        poller._online()
    assert "Remote Config poll failed; retaining applied configuration; retrying in 0.500s" in caplog.messages
    assert "Remote Config delivery recovered after 1 failed poll(s)" in caplog.messages
