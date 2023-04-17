# -*- coding: utf-8 -*-

import mock
from mock.mock import MagicMock
import pytest

from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import RemoteConfigCallBack
from ddtrace.internal.remoteconfig.client import RemoteConfigCallBackAfterMerge
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.client import RemoteConfigError
from ddtrace.internal.remoteconfig.client import TargetFile


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_update_applied_configs(mock_extract_target_file):
    mock_config_content = {"test": "content"}
    mock_extract_target_file.return_value = mock_config_content
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
    mock_callback.assert_called_once_with(mock_config, mock_config_content)
    assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_dispatch_applied_configs(mock_extract_target_file):
    class RCAppSecCallBack(RemoteConfigCallBackAfterMerge):
        configs = {}

        def __call__(self, metadata, features):
            mock_callback(metadata, features)

    expected_results = {}

    class MockExtractFile:
        counter = 1

        def __call__(self, payload, target, config):
            self.counter += 1
            result = {"test{}".format(self.counter): [target]}
            expected_results.update(result)
            return result

    mock_extract_target_file.side_effect = MockExtractFile()
    mock_callback = MagicMock()
    callback = RCAppSecCallBack()

    applied_configs = {}
    payload = {}
    client_configs = {
        "mock/ASM_FEATURES": ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        ),
        "mock/ASM_DATA": ConfigMetadata(
            id="", product_name="ASM_DATA", sha256_hash="sha256_hash", length=5, tuf_version=5
        ),
    }

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_DATA", callback)
    rc_client.register_product("ASM_FEATURES", callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_callback.assert_called_once_with("", expected_results)
    assert applied_configs == client_configs
    rc_client._products = {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_config_exists(mock_extract_target_file):
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)
    rc_client._applied_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_not_called()
    mock_callback.assert_not_called()
    assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_extract_target_file(mock_extract_target_file):
    mock_extract_target_file.return_value = None
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
    mock_callback.assert_not_called()
    assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_callback(mock_extract_target_file):
    class RemoteConfigCallbackTestException(Exception):
        pass

    def exception_callback():
        raise RemoteConfigCallbackTestException("error")

    mock_config_content = {"test": "content"}
    mock_extract_target_file.return_value = mock_config_content
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", exception_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)

    # An exception prevents the configuration from being applied
    assert applied_configs["mock/ASM_FEATURES"].apply_state in (1, 3)


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


def test_apply_default_callback():
    class callbackClass(RemoteConfigCallBack):
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    callback = callbackClass()
    callback_content = {"a": 1}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)

    assert callback.result == ({"Config": "data"}, {"a": 1})
    assert test_list_callbacks == []


def test_apply_merge_callback():
    class callbackClass(RemoteConfigCallBackAfterMerge):
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    callback = callbackClass()
    callback_content = {"a": [1]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)

    assert len(test_list_callbacks) == 1
    test_list_callbacks[0].dispatch()

    assert callback.result == ("", {"a": [1]})


def test_apply_merge_multiple_callback():
    class callbackClass(RemoteConfigCallBackAfterMerge):
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    callback1 = callbackClass()
    callback2 = callbackClass()
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback2, callback_content2, target, config)

    assert len(test_list_callbacks) == 1
    test_list_callbacks[0].dispatch()

    assert callback1.result == ("", {"a": [1], "b": [2]})
    assert callback2.result is None


def test_apply_merge_different_callback():
    class callback1And2Class(RemoteConfigCallBackAfterMerge):
        configs = {}
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    class callback3Class(RemoteConfigCallBackAfterMerge):
        configs = {}
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    callback1 = callback1And2Class()
    callback2 = callback1And2Class()
    callback3 = callback3Class()
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback2, callback_content2, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content2, target, config)

    assert len(test_list_callbacks) == 2
    test_list_callbacks[0].dispatch()
    test_list_callbacks[1].dispatch()

    assert callback1.result == ("", {"a": [1], "b": [2]})
    assert callback2.result is None
    assert callback3.result == ("", {"b": [2]})


def test_apply_merge_different_target_callback():
    class callback1And2Class(RemoteConfigCallBackAfterMerge):
        configs = {}
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    class callback3Class(RemoteConfigCallBackAfterMerge):
        configs = {}
        result = None

        def __call__(self, *args, **kwargs):
            self.result = args

    callback1 = callback1And2Class()
    callback2 = callback1And2Class()
    callback3 = callback3Class()
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    callback_content3 = {"b": [3]}
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, "1/ASM/1", config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback2, callback_content2, "1/ASM/2", config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content3, "1/ASM/3", config)

    assert len(test_list_callbacks) == 2
    test_list_callbacks[0].dispatch()
    test_list_callbacks[1].dispatch()

    assert callback1.result == ("", {"a": [1], "b": [2]})
    assert callback2.result is None
    assert callback3.result == ("", {"b": [3]})
