# -*- coding: utf-8 -*-
from unittest import mock

from mock.mock import MagicMock
import pytest

from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig.client import AgentPayload
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.client import RemoteConfigError
from ddtrace.internal.remoteconfig.client import TargetFile
from tests.utils import override_global_config


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_update_applied_configs(mock_extract_target_file):
    """Test that new configurations are loaded, payloads are created, and applied_configs is updated."""
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
        rc_client.register_product("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._load_new_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        # Verify extraction was called
        mock_extract_target_file.assert_called_with(agent_payload, "mock/ASM_FEATURES", mock_config)

        # Verify a payload was created and published
        assert len(payload_list) == 1
        assert payload_list[0].content == mock_config_content
        assert payload_list[0].path == "mock/ASM_FEATURES"
        assert payload_list[0].metadata == mock_config

        # Verify applied_configs was updated
        assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_multiple_products_same_callback(mock_extract_target_file):
    """Test that multiple products can share the same callback and all payloads are collected."""
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()

        # Mock extract to return different content for each product
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
        # Register the same callback for both products
        rc_client.register_product("ASM_DATA", mock_callback)
        rc_client.register_product("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._load_new_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        # Verify both payloads were created
        assert len(payload_list) == 2

        # Verify payloads contain correct data from both products
        products = {p.metadata.product_name for p in payload_list}
        assert products == {"ASM_FEATURES", "ASM_DATA"}

        # Verify applied_configs was updated with both configs
        assert len(applied_configs) == 2
        assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_config_exists(mock_extract_target_file):
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        agent_payload = AgentPayload()
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_FEATURES", mock_callback)
        rc_client._applied_configs = {"mock/ASM_FEATURES": mock_config}

        payload_list = []
        rc_client._load_new_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        mock_extract_target_file.assert_not_called()
        # callback is not called during load - it happens in subscriber
        assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_extract_target_file(mock_extract_target_file):
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
        rc_client.register_product("ASM_FEATURES", mock_callback)

        payload_list = []
        rc_client._load_new_configurations(payload_list, applied_configs, client_configs, payload=agent_payload)
        rc_client._publish_configuration(payload_list)

        mock_extract_target_file.assert_called_with(agent_payload, "mock/ASM_FEATURES", mock_config)
        mock_callback.assert_not_called()
        assert applied_configs == {}


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
