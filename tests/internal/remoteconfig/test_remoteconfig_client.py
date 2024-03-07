# -*- coding: utf-8 -*-
import time

import mock
from mock.mock import ANY
from mock.mock import MagicMock
import pytest

from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeDicts
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.client import RemoteConfigError
from ddtrace.internal.remoteconfig.client import TargetFile
from tests.utils import override_global_config


class RCClientMockPubSub(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "TESTS")


class RCClientMockPubSub2(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "TESTS")


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_update_applied_configs(mock_extract_target_file):
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_config_content = {"test": "content"}
        mock_extract_target_file.return_value = mock_config_content
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        payload = {}
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_FEATURES", mock_callback)

        list_callbacks = []
        rc_client._load_new_configurations(list_callbacks, applied_configs, client_configs, payload=payload)
        rc_client._publish_configuration(list_callbacks)

        mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
        mock_callback.append.assert_called_once_with(mock_config_content, "mock/ASM_FEATURES", mock_config)
        mock_callback.publish.assert_called_once()
        assert applied_configs == client_configs


# TODO: split this test into smaller tests that operate independently from each other
@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_dispatch_applied_configs(mock_extract_target_file):
    with override_global_config(dict(_remote_config_poll_interval=0.1, _remote_config_enabled=True)):
        mock_callback = MagicMock()

        def _mock_appsec_callback(features, test_tracer=None):
            mock_callback(dict(features))

        class MockExtractFile:
            counter = 1

            def __call__(self, payload, target, config):
                self.counter += 1
                result = {"test{}".format(self.counter): [target]}
                expected_results.update(result)
                return result

        mock_extract_target_file.side_effect = MockExtractFile()

        expected_results = {}
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

        asm_callback = RCClientMockPubSub(None, _mock_appsec_callback)
        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_DATA", asm_callback)
        rc_client.register_product("ASM_FEATURES", asm_callback)
        asm_callback.start_subscriber()

        list_callbacks = []
        rc_client._load_new_configurations(list_callbacks, applied_configs, client_configs, payload=payload)
        rc_client._publish_configuration(list_callbacks)
        time.sleep(0.5)

        mock_callback.assert_called_once_with({"metadata": {}, "config": expected_results, "shared_data_counter": ANY})
        assert applied_configs == client_configs
        rc_client._products = {}
        asm_callback.stop()

        mock_callback = mock.MagicMock()

        def _mock_appsec_callback(features, test_tracer=None):
            mock_callback(features)

        callback_content = {"b": [1, 2, 3]}
        target = "1/ASM/2"
        config = {"Config": "data"}
        test_list_callbacks = []
        callback = RCClientMockPubSub(None, _mock_appsec_callback)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)
        callback.start_subscriber()
        for callback_to_dispach in test_list_callbacks:
            callback_to_dispach.publish()
        time.sleep(0.5)

        mock_callback.assert_called_with({"metadata": {}, "config": {"b": [1, 2, 3]}, "shared_data_counter": 2})
        assert len(test_list_callbacks) > 0
        callback.stop()

        class callbackClass:
            result = None

            @classmethod
            def _mock_appsec_callback(cls, *args, **kwargs):
                cls.result = dict(args[0])

        callback1 = RCClientMockPubSub(None, callbackClass._mock_appsec_callback)
        callback2 = RCClientMockPubSub2(None, callbackClass._mock_appsec_callback)
        callback_content1 = {"d": [1]}
        callback_content2 = {"e": [2]}
        target = "1/ASM/3"
        config = {"Config": "data"}
        test_list_callbacks = []
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
        callback1.start_subscriber()
        callback2.start_subscriber()
        assert len(test_list_callbacks) == 1
        test_list_callbacks[0].publish()
        time.sleep(0.5)

        assert callbackClass.result == {"config": {"d": [1], "e": [2]}, "metadata": {}, "shared_data_counter": ANY}
        callback1.stop()
        callback2.stop()

    class Callback1And2Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    class Callback3Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    with override_global_config(dict(_remote_config_poll_interval=0.1)):
        callback1 = RCClientMockPubSub(None, Callback1And2Class._mock_appsec_callback)
        callback3 = RCClientMockPubSub2(None, Callback3Class._mock_appsec_callback)
        callback_content1 = {"f": [1]}
        callback_content2 = {"g": [2]}
        callback_content3 = {"g": [3]}
        target = "1/ASM/2"
        config = {"Config": "data"}
        test_list_callbacks = []
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content3, target, config)
        callback1.start_subscriber()
        callback3.start_subscriber()
        assert len(test_list_callbacks) == 2
        test_list_callbacks[0].publish()
        test_list_callbacks[1].publish()
        time.sleep(0.5)

        assert Callback3Class.result == {"config": {"g": [3]}, "metadata": {}, "shared_data_counter": ANY}
        assert Callback1And2Class.result == {"config": {"f": [1], "g": [2]}, "metadata": {}, "shared_data_counter": ANY}
        callback1.stop()
        callback3.stop()

        class Callback1And2Class:
            result = None

            @classmethod
            def _mock_appsec_callback(cls, *args, **kwargs):
                cls.result = dict(args[0])

        class Callback3Class:
            result = None

            @classmethod
            def _mock_appsec_callback(cls, *args, **kwargs):
                cls.result = dict(args[0])

        callback1 = RCClientMockPubSub(None, Callback1And2Class._mock_appsec_callback)
        callback3 = RCClientMockPubSub2(None, Callback3Class._mock_appsec_callback)
        callback_content1 = {"a": [1]}
        callback_content2 = {"b": [2]}
        callback_content3 = {"c": [2]}
        target = "1/ASM/2"
        config = {"Config": "data"}
        test_list_callbacks = []
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
        RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content3, target, config)
        callback1.start_subscriber()
        callback3.start_subscriber()
        assert len(test_list_callbacks) == 2
        test_list_callbacks[0].publish()
        test_list_callbacks[1].publish()
        time.sleep(0.5)

        assert Callback3Class.result == {"config": {"c": [2]}, "metadata": {}, "shared_data_counter": ANY}
        assert Callback1And2Class.result == {"config": {"a": [1], "b": [2]}, "metadata": {}, "shared_data_counter": ANY}
        callback1.stop()
        callback3.stop()


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_config_exists(mock_extract_target_file):
    with override_global_config(dict(_remote_config_enabled=True)):
        mock_callback = MagicMock()
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        payload = {}
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_FEATURES", mock_callback)
        rc_client._applied_configs = {"mock/ASM_FEATURES": mock_config}

        list_callbacks = []
        rc_client._load_new_configurations(list_callbacks, applied_configs, client_configs, payload=payload)
        rc_client._publish_configuration(list_callbacks)

        mock_extract_target_file.assert_not_called()
        mock_callback.assert_not_called()
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
        payload = {}
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_FEATURES", mock_callback)

        list_callbacks = []
        rc_client._load_new_configurations(list_callbacks, applied_configs, client_configs, payload=payload)
        rc_client._publish_configuration(list_callbacks)

        mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
        mock_callback.assert_not_called()
        assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_callback(mock_extract_target_file):
    class RemoteConfigCallbackTestException(Exception):
        pass

    def exception_callback():
        raise RemoteConfigCallbackTestException("error")

    with override_global_config(dict(_remote_config_enabled=True)):
        mock_config_content = {"test": "content"}
        mock_extract_target_file.return_value = mock_config_content
        mock_config = ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        )

        applied_configs = {}
        payload = {}
        client_configs = {"mock/ASM_FEATURES": mock_config}

        rc_client = RemoteConfigClient()
        rc_client.register_product("ASM_FEATURES", exception_callback)

        list_callbacks = []
        rc_client._load_new_configurations(list_callbacks, applied_configs, client_configs, payload=payload)
        rc_client._publish_configuration(list_callbacks)

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
    class CallbackClass:
        config = None
        result = None
        _publisher = None

        @classmethod
        def append(cls, *args, **kwargs):
            cls.config = dict(args[2])
            cls.result = dict(args[0])

        @classmethod
        def publish(cls):
            pass

    callback_content = {"a": 1}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    callback = CallbackClass()
    RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)

    assert CallbackClass.config == config
    assert CallbackClass.result == callback_content
    assert test_list_callbacks == [callback]
