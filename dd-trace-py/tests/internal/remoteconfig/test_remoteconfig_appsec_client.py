# -*- coding: utf-8 -*-
import mock
from mock.mock import MagicMock
import pytest

from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.client import RemoteConfigError
from ddtrace.internal.remoteconfig.client import TargetFile
from tests.utils import override_global_config


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_reconcile_applies_new_configuration(mock_extract_target_file):
    """A new incoming config not in _applied_configs should be extracted, emitted as a
    payload, and recorded in applied_configs.
    """
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_config_content = {"test": "content"}
        mock_extract_target_file.return_value = mock_config_content
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        agent_payload = AgentPayload()
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        mock_extract_target_file.assert_called_with(agent_payload, "mock/ASM_FEATURES", mock_config)

        assert len(payload_list) == 1
        assert payload_list[0].content == mock_config_content
        assert payload_list[0].path == "mock/ASM_FEATURES"
        assert payload_list[0].metadata == mock_config

        assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_reconcile_multiple_products_same_callback(mock_extract_target_file):
    """Multiple products sharing a callback should each produce their own payload."""
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()

        def mock_extract(payload, target, config):
            return {"product": config.product_name, "target": target}

        mock_extract_target_file.side_effect = mock_extract

        applied_configs = {}
        client_configs = {
            "mock/ASM_FEATURES": ConfigMetadata(
                id="1", product_name="ASM_FEATURES", sha256_hash="hash1", length=5, tuf_version=5
            ),
            "mock/ASM_DATA": ConfigMetadata(
                id="2", product_name="ASM_DATA", sha256_hash="hash2", length=5, tuf_version=5
            ),
        }

        agent_payload = AgentPayload()

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_DATA", mock_callback)
        rc_client.register_callback("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        assert len(payload_list) == 2

        products = {p.metadata.product_name for p in payload_list}
        assert products == {"ASM_FEATURES", "ASM_DATA"}

        assert len(applied_configs) == 2
        assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_reconcile_unchanged_configuration_skips_extraction(mock_extract_target_file):
    """A config already in _applied_configs and matching client_configs should be
    carried over without triggering extraction.
    """
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        agent_payload = AgentPayload()
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_FEATURES", mock_callback)
        rc_client._applied_configs = {"mock/ASM_FEATURES": mock_config}

        payload_list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        mock_extract_target_file.assert_not_called()
        assert payload_list == []
        assert applied_configs == {"mock/ASM_FEATURES": mock_config}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_reconcile_extract_returns_none_skips_apply(mock_extract_target_file):
    """When _extract_target_file returns None, the config is not queued and does not
    enter applied_configs.
    """
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_extract_target_file.return_value = None
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        agent_payload = AgentPayload()
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        mock_extract_target_file.assert_called_with(agent_payload, "mock/ASM_FEATURES", mock_config)
        mock_callback.assert_not_called()
        assert payload_list == []
        assert applied_configs == {}


def test_reconcile_emits_disable_when_unassigned_from_client():
    """A target previously applied but no longer present in client_configs must generate
    a disable payload (content=None) so the product learns it was removed, regardless
    of whether the config is still present elsewhere in the signed targets.
    """
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()
        applied_config = ConfigMetadata(id="cfg", product_name="ASM_FEATURES", sha256_hash="h", length=1, tuf_version=1)

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_FEATURES", mock_callback)
        rc_client._applied_configs = {"datadog/2/ASM_FEATURES/cfg/config": applied_config}

        applied_configs: dict = {}
        payload_list: list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs={}, payload=AgentPayload())

        assert len(payload_list) == 1
        assert payload_list[0].path == "datadog/2/ASM_FEATURES/cfg/config"
        assert payload_list[0].content is None
        assert payload_list[0].metadata is applied_config
        assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_reconcile_emits_disables_before_applies(mock_extract_target_file):
    """Disables for removed targets must precede apply payloads for new/changed ones so
    product callbacks observe removals first.
    """
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_extract_target_file.return_value = {"ok": True}
        mock_callback = MagicMock()

        old_cfg = ConfigMetadata(id="old", product_name="ASM_FEATURES", sha256_hash="h0", length=1, tuf_version=1)
        kept_cfg = ConfigMetadata(id="k", product_name="ASM_FEATURES", sha256_hash="hk", length=1, tuf_version=1)
        new_cfg = ConfigMetadata(id="new", product_name="ASM_FEATURES", sha256_hash="hn", length=1, tuf_version=1)
        changed_cfg_old = ConfigMetadata(id="c", product_name="ASM_FEATURES", sha256_hash="hc", length=1, tuf_version=1)
        changed_cfg_new = ConfigMetadata(
            id="c", product_name="ASM_FEATURES", sha256_hash="hc2", length=1, tuf_version=2
        )

        rc_client = RemoteConfigClient()
        rc_client.register_callback("ASM_FEATURES", mock_callback)
        rc_client._applied_configs = {
            "datadog/2/ASM_FEATURES/old/config": old_cfg,
            "datadog/2/ASM_FEATURES/k/config": kept_cfg,
            "datadog/2/ASM_FEATURES/c/config": changed_cfg_old,
        }

        client_configs = {
            "datadog/2/ASM_FEATURES/k/config": kept_cfg,
            "datadog/2/ASM_FEATURES/c/config": changed_cfg_new,
            "datadog/2/ASM_FEATURES/new/config": new_cfg,
        }

        applied_configs: dict = {}
        payload_list: list = []
        rc_client._reconcile_configurations(payload_list, applied_configs, client_configs, payload=AgentPayload())

        # Find the index of the disable and of any apply payload.
        disable_indices = [i for i, p in enumerate(payload_list) if p.content is None]
        apply_indices = [i for i, p in enumerate(payload_list) if p.content is not None]

        assert len(disable_indices) == 1, "expected a single disable payload for the removed target"
        assert payload_list[disable_indices[0]].path == "datadog/2/ASM_FEATURES/old/config"
        assert len(apply_indices) == 2, "expected apply payloads for changed and new"
        # All disables must come before any apply.
        assert max(disable_indices) < min(apply_indices)

        # Unchanged config carried over; changed and new are in applied_configs.
        assert applied_configs["datadog/2/ASM_FEATURES/k/config"] is kept_cfg
        assert applied_configs["datadog/2/ASM_FEATURES/c/config"] is changed_cfg_new
        assert applied_configs["datadog/2/ASM_FEATURES/new/config"] is new_cfg
        assert "datadog/2/ASM_FEATURES/old/config" not in applied_configs


@pytest.mark.parametrize(
    "payload_client_configs,num_payload_target_files,cache_target_files,expected_result_ok",
    [
        (
            [
                "target/path/0",
            ],
            1,
            {},
            True,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            {},
            True,
        ),
        (
            [
                "target/path/2",
            ],
            3,
            {},
            True,
        ),
        (
            [
                "target/path/6",
            ],
            3,
            {},
            False,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            [{"path": "target/path/1"}],
            True,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            [{"path": "target/path/1"}, {"path": "target/path/2"}],
            True,
        ),
        (
            [
                "target/path/1",
            ],
            0,
            [{"path": "target/path/1"}],
            True,
        ),
        (
            [
                "target/path/2",
            ],
            0,
            [{"path": "target/path/1"}],
            False,
        ),
        (
            [
                "target/path/0",
                "target/path/1",
            ],
            1,
            [{"path": "target/path/1"}],
            True,
        ),
        (["target/path/0", "target/path/1", "target/path/6"], 2, [{"path": "target/path/6"}], True),
    ],
)
def test_validate_config_exists_in_target_paths(
    payload_client_configs, num_payload_target_files, cache_target_files, expected_result_ok
):
    def build_payload_target_files(num_payloads):
        payload_target_files = []
        for i in range(num_payloads):
            mock = TargetFile(path="target/path/%s" % i, raw="")
            payload_target_files.append(mock)
        return payload_target_files

    rc_client = RemoteConfigClient()
    rc_client.cached_target_files = cache_target_files

    payload_target_files = build_payload_target_files(num_payload_target_files)

    if expected_result_ok:
        rc_client._validate_config_exists_in_target_paths(payload_client_configs, payload_target_files)
    else:
        with pytest.raises(RemoteConfigError):
            rc_client._validate_config_exists_in_target_paths(payload_client_configs, payload_target_files)


@pytest.mark.subprocess(env={"DD_TAGS": "env:foo,version:bar"})
def test_remote_config_client_tags():
    from ddtrace.internal.remoteconfig.client import RemoteConfigClient

    tags = dict(_.split(":", 1) for _ in RemoteConfigClient()._client_tracer["tags"])

    assert tags["env"] == "foo"
    assert tags["version"] == "bar"


@pytest.mark.subprocess(
    env={"DD_TAGS": "env:foooverridden,version:baroverridden", "DD_ENV": "foo", "DD_VERSION": "bar"}
)
def test_remote_config_client_tags_override():
    from ddtrace.internal.remoteconfig.client import RemoteConfigClient

    tags = dict(_.split(":", 1) for _ in RemoteConfigClient()._client_tracer["tags"])

    assert tags["env"] == "foo"
    assert tags["version"] == "bar"


# test_apply_default_callback removed - _apply_callback method no longer exists in new architecture
