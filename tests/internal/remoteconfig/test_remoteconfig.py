# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
from logging import Logger
import multiprocessing
import os
import sys
from time import sleep

import mock
from mock.mock import ANY
import pytest

from ddtrace import config
from ddtrace.internal.constants import DDTRACE_FILE_HANDLER_NAME
from ddtrace.internal.constants import TRACER_FLARE_DIRECTORY
from ddtrace.internal.logger import DDLogger
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeDicts
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig._subscribers import TracerFlareSubscriber
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatus
from tests.internal.test_utils_version import _assert_and_get_version_agent_format
from tests.utils import override_global_config


class TracerFlareMockPubSub(PubSub):
    __subscriber_class__ = TracerFlareSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "TESTS")


class RCMockPubSub(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "TESTS")


def to_bytes(string):
    return bytes(string, encoding="utf-8")


def to_str(bytes_string):
    return str(bytes_string, encoding="utf-8")


def get_mock_encoded_msg(msg):
    expires_date = datetime.datetime.strftime(
        datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
    )
    path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": {
            "_type": "targets",
            "custom": {"opaque_backend_state": ""},
            "expires": expires_date,
            "spec_version": "1.0.0",
            "targets": {
                path: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                    "length": 24,
                }
            },
            "version": 0,
        },
    }
    return {
        "roots": [
            to_str(
                base64.b64encode(
                    to_bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                    )
                )
            )
        ],
        "targets": to_str(base64.b64encode(to_bytes(json.dumps(data)))),
        "target_files": [
            {
                "path": path,
                "raw": to_str(base64.b64encode(msg)),
            }
        ],
        "client_configs": [path],
    }


def get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors):
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": signed_errors,
    }
    return {
        "roots": [
            to_str(
                base64.b64encode(
                    to_bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                    )
                )
            )
        ],
        "targets": to_str(base64.b64encode(to_bytes(json.dumps(data)))),
        "target_files": [
            {
                "path": path,
                "raw": to_str(base64.b64encode(msg)),
            }
        ],
        "client_configs": [path],
    }


def test_remote_config_register_auto_enable(remote_config_worker):
    # ASM_FEATURES product is enabled by default, but LIVE_DEBUGGER isn't
    class MockPubsub(PubSub):
        def stop(self, *args, **kwargs):
            pass

    mock_pubsub = MockPubsub()
    remoteconfig_poller.disable()
    with override_global_config(dict(_remote_config_enabled=True)):
        assert remoteconfig_poller.status == ServiceStatus.STOPPED

        remoteconfig_poller.register("LIVE_DEBUGGER", mock_pubsub)

        assert remoteconfig_poller.status == ServiceStatus.RUNNING
        assert remoteconfig_poller._client._products["LIVE_DEBUGGER"] is not None

        remoteconfig_poller.disable()


def test_remote_config_register_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()

    class MockPubsub(PubSub):
        def stop(self, *args, **kwargs):
            pass

    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.register("LIVE_DEBUGGER", MockPubsub())

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


def test_remote_config_enable_validate_rc_disabled(remote_config_worker):
    remoteconfig_poller.disable()
    assert remoteconfig_poller.status == ServiceStatus.STOPPED

    with override_global_config(dict(_remote_config_enabled=False)):
        remoteconfig_poller.enable()

        assert remoteconfig_poller.status == ServiceStatus.STOPPED


@pytest.mark.skipif(
    sys.version_info >= (3, 12, 0),
    reason="Python 3.12 subprocess will raise deprecation warning for forking in a multi-threaded process",
)
@pytest.mark.subprocess(env=dict(DD_REMOTE_CONFIGURATION_ENABLED="true"))
def test_remote_config_forksafe():
    import os

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
    from ddtrace.internal.service import ServiceStatus

    remoteconfig_poller.enable()

    parent_worker = remoteconfig_poller
    assert parent_worker.status == ServiceStatus.RUNNING

    client_id = remoteconfig_poller._client.id
    runtime_id = remoteconfig_poller._client._client_tracer["runtime_id"]

    parent_payload = remoteconfig_poller._client._build_payload(None)

    assert client_id == parent_payload["client"]["id"]
    assert runtime_id == parent_payload["client"]["client_tracer"]["runtime_id"]

    if os.fork() == 0:
        assert remoteconfig_poller.status == ServiceStatus.RUNNING
        assert remoteconfig_poller._worker is not parent_worker

        child_payload = remoteconfig_poller._client._build_payload(None)

        assert client_id != child_payload["client"]["id"]
        assert runtime_id != child_payload["client"]["client_tracer"]["runtime_id"]
        exit(0)


# TODO: split this test into smaller tests that operate independently from each other
@mock.patch.object(RemoteConfigClient, "_send_request")
def test_remote_configuration_1_click(mock_send_request, remote_config_worker):
    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)

            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {
                "config": {"asm": {"enabled": True}},
                "metadata": {},
                "shared_data_counter": ANY,
            }

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = dict(features)

    callback = Callback()

    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.1)):
        mock_send_request.return_value = get_mock_encoded_msg(
            b'{"rules_data": [{"data": [{"expiration": 1662804872, "value": "127.0.0.0"}, '
            b'{"expiration": 1662804872, "value": "52.80.198.1"}], "id": "blocking_ips", '
            b'"type": "ip_with_expiration"}]}'
        )
        with RemoteConfigPoller() as rc:
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {
                "config": {
                    "rules_data": [
                        {
                            "data": [
                                {"expiration": 1662804872, "value": "127.0.0.0"},
                                {"expiration": 1662804872, "value": "52.80.198.1"},
                            ],
                            "id": "blocking_ips",
                            "type": "ip_with_expiration",
                        }
                    ]
                },
                "metadata": {},
                "shared_data_counter": ANY,
            }

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            msg = b'{"asm":{"enabled":true}}'
            expires_date = datetime.datetime.strftime(
                datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
            )
            path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
            # Signed data without version `spec_version`
            signed_errors = {
                "_type": "targets",
                "custom": {"opaque_backend_state": ""},
                "expires": expires_date,
                "targets": {
                    path: {
                        "custom": {"c": [""], "v": 0},
                        "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                        "length": 24,
                    }
                },
                "version": 0,
            }
            mock_send_request.return_value = get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors)
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert callback.features == {}
            assert rc._client._last_error == "invalid agent payload received"

    class Callback:
        features = {}

        def _reload_features(self, features, test_tracer=None):
            self.features = features

    callback = Callback()
    with override_global_config(dict(_remote_config_enabled=True, _remote_config_poll_interval=0.5)):
        with RemoteConfigPoller() as rc:
            mock_pubsub = RCMockPubSub(None, callback._reload_features)
            rc.register(ASM_FEATURES_PRODUCT, mock_pubsub)
            for _ in range(0, 2):
                msg = b'{"asm":{"enabled":true}}'
                expires_date = datetime.datetime.strftime(
                    datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
                )
                path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
                # Signed data without version `spec_version`
                signed_errors = {
                    "_type": "targets",
                    "custom": {"opaque_backend_state": ""},
                    "expires": expires_date,
                    "targets": {
                        path: {
                            "custom": {"c": [""], "v": 0},
                            "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                            "length": 24,
                        }
                    },
                    "version": 0,
                }
                mock_send_request.return_value = get_mock_encoded_msg_with_signed_errors(msg, path, signed_errors)
                rc._online()
                mock_send_request.assert_called()
                sleep(0.5)
                assert callback.features == {}
                assert rc._client._last_error == "invalid agent payload received"

            mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
            rc._online()
            mock_send_request.assert_called()
            sleep(0.5)
            assert rc._client._last_error is None
            assert callback.features == {
                "config": {"asm": {"enabled": True}},
                "metadata": {},
                "shared_data_counter": ANY,
            }


def test_remoteconfig_semver():
    _assert_and_get_version_agent_format(RemoteConfigClient()._client_tracer["tracer_version"])


@pytest.mark.parametrize(
    "result,expected",
    [
        (None, False),
        ({}, False),
        ({"endpoints": []}, False),
        ({"endpoints": ["/info"]}, False),
        ({"endpoints": ["/info", "/errors"]}, False),
        ({"endpoints": ["/info", "/errors", REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
        ({"endpoints": ["/info", "/errors", "/" + REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
    ],
)
@mock.patch("ddtrace.internal.agent.info")
def test_remote_configuration_check_remote_config_enable_in_agent_errors(
    mock_info, result, expected, remote_config_worker
):
    mock_info.return_value = result

    worker = RemoteConfigPoller()

    # Check that the initial state is agent_check
    assert worker._state == worker._agent_check

    worker.periodic()

    # Check that the state is online if the agent supports remote config
    assert worker._state == worker._online if expected else worker._agent_check
    worker.stop_subscribers(True)
    worker.disable()


@pytest.mark.subprocess(
    parametrize=dict(
        DD_REMOTE_CONFIGURATION_ENABLED=["1", "0"],
    ),
)
def test_rc_default_products_registered():
    """
    By default, RC should be enabled. When RC is enabled, we will always
    enable the tracer flare feature as well. There should be three products
    registered when DD_REMOTE_CONFIGURATION_ENABLED is True
    """
    import os

    from ddtrace.internal.utils.formats import asbool

    rc_enabled = asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED"))

    # Import this to trigger the preload
    from ddtrace import config
    import ddtrace.auto  # noqa:F401

    assert config._remote_config_enabled == rc_enabled

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    assert bool(remoteconfig_poller._client._products.get("APM_TRACING")) == rc_enabled
    # Required for tracer flare
    assert bool(remoteconfig_poller._client._products.get("AGENT_CONFIG")) == rc_enabled
    assert bool(remoteconfig_poller._client._products.get("AGENT_TASK")) == rc_enabled


agent_config = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
# agent_task = [
#     False,
#     {
#         "args": {
#             "case_id": "1111111",
#             "hostname": "myhostname",
#             "user_handle": "user.name@datadoghq.com",
#         },
#         "task_type": "tracer_flare",
#         "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
#     },
# ]

# def tearDown():
#     # Ensure files are cleaned up
#     # config._clean_up_tracer_flare_files()
#     # config._revert_tracer_flare_configs()
#     pass


def confirm_cleanup(logger: DDLogger):
    assert not os.path.exists(TRACER_FLARE_DIRECTORY), f"The directory {TRACER_FLARE_DIRECTORY} still exists"
    assert logger._getHandler(DDTRACE_FILE_HANDLER_NAME) is None


def test_tracer_flare_single_process_success():
    """
    Ensure the baseline tracer flare works for a single process
    AGENT_CONFIG expected behavior:
    - file handler added to logger
    - correct log level set for handler and logger
    - logs added to file

    AGENT_TASK expected behavior:
    - file handler removed from logger
    - log level reverted for logger
    - new logs are not being added to file
    - generated files are cleaned up
    """
    logger = get_logger("ddtrace.settings.config")
    assert type(logger) == DDLogger
    assert logger.level == 0  # set to WARN initially

    config._handle_agent_config_product(agent_config)

    file_handler = logger._getHandler("ddtrace_file_handler")
    assert file_handler is not None
    assert file_handler.level == 10
    assert logger.level == 10

    pid = os.getpid()
    flare_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_python_{pid}.log"

    assert os.path.exists(flare_file_path)

    # Generate a few dummy tracer logs
    for n in range(10):
        if n % 2 == 0:
            logger.debug(n)
        else:
            logger.info(n)

    agent_task = [
        False,
        {
            "args": {
                "case_id": "1111111",
                "hostname": "myhostname",
                "user_handle": "user.name@datadoghq.com",
            },
            "task_type": "tracer_flare",
            "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
        },
    ]

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        config._handle_agent_task_product(agent_task)
        confirm_cleanup(logger)


def test_tracer_flare_single_process_partial_failure():
    """
    Validate that even if one of the files fails to be generated,
    we still attempt to send the flare with partial info (ensure best effort)
    """
    configs = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
    logger = get_logger("ddtrace.settings.config")
    assert logger.level == 0  # set to WARN initially

    config._handle_agent_config_product(configs)

    file_handler = logger._getHandler(DDTRACE_FILE_HANDLER_NAME)
    assert file_handler is not None
    assert file_handler.level == 10
    assert logger.level == 10

    pid = os.getpid()
    flare_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_python_{pid}.log"

    assert os.path.exists(flare_file_path)

    # Generate a few dummy logs
    for n in range(10):
        if n % 2 == 0:
            logger.debug(n)
        else:
            logger.info(n)

    task_configs = [
        False,
        {
            "args": {
                "case_id": "1111111",
                "hostname": "myhostname",
                "user_handle": "user.name@datadoghq.com",
            },
            "task_type": "tracer_flare",
            "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
        },
    ]

    with mock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        config._handle_agent_task_product(task_configs)

    confirm_cleanup(logger)


def test_tracer_flare_multiple_process():
    """
    Ensure the tracer flare will generate for multiple processes
    """
    processes = []
    num_processes = 3
    agent_config = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
    agent_task = [
        False,
        {
            "args": {
                "case_id": "1111111",
                "hostname": "myhostname",
                "user_handle": "user.name@datadoghq.com",
            },
            "task_type": "tracer_flare",
            "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
        },
    ]

    def do_tracer_flare(agent_config, agent_task):
        config._handle_agent_config_product(agent_config)
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            config._handle_agent_task_product(agent_task)

    # Create multiple processes
    for _ in range(num_processes):
        p = multiprocessing.Process(target=do_tracer_flare, args=(agent_config, agent_task))
        processes.append(p)
        p.start()

        # Wait for all processes to complete
        for p in processes:
            p.join()

        # Assert that each process wrote its file successfully
        # We double the process number because each will generate a log file and a config file
        assert len(processes) * 2 == len(os.listdir(TRACER_FLARE_DIRECTORY))

    confirm_cleanup(get_logger("ddtrace.settings.config"))


def test_tracer_flare_multiple_process_partial_failure():
    """
    Even if the tracer flare fails for one process, we should
    still continue the work for the other processes (ensure best effort)
    """
    processes = []
    agent_config = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
    agent_task = [
        False,
        {
            "args": {
                "case_id": "111111",
                "hostname": "myhostname",
                "user_handle": "user.name@datadoghq.com",
            },
            "task_type": "tracer_flare",
            "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
        },
    ]

    def do_tracer_flare(agent_config, agent_task):
        config._handle_agent_config_product(agent_config)
        # Assert that only one process wrote its file successfully
        # We check for 2 files because it will generate a log file and a config file
        assert 2 == len(os.listdir(TRACER_FLARE_DIRECTORY))

        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            config._handle_agent_task_product(agent_task)

    # Create successful process
    p = multiprocessing.Process(target=do_tracer_flare, args=(agent_config, agent_task))
    processes.append(p)
    p.start()

    # Create failing process
    p = multiprocessing.Process(target=do_tracer_flare, args=(None, agent_task))
    processes.append(p)
    p.start()

    # Wait for all processes to complete
    for p in processes:
        p.join()

    confirm_cleanup(get_logger("ddtrace.settings.config"))


def test_tracer_flare_no_app_logs():
    """
    Check that app logs are not being added to the
    file, just the tracer logs
    """
    configs = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
    app_logger = Logger(name="my-app", level=10)  # DEBUG level
    config._handle_agent_config_product(configs)

    app_log_line = "this is an app log"
    app_logger.debug(app_log_line)

    pid = os.getpid()
    flare_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_python_{pid}.log"
    assert os.path.exists(flare_file_path)

    with open(flare_file_path, "r") as file:
        for line in file:
            assert app_log_line not in line, f"File {flare_file_path} contains excluded line: {app_log_line}"


def test_tracer_flare_fallback_send_and_clean():
    """
    Ensure we clean up and revert configurations if a tracer
    flare job has gone stale (>= 20 mins)
    """
    tracer_flare_sub = TracerFlareSubscriber(
        data_connector=PublisherSubscriberConnector(), callback=config._handle_tracerflare
    )

    # Generate an AGENT_CONFIG product to trigger a request
    with mock.patch("ddtrace.internal.remoteconfig._connectors.PublisherSubscriberConnector.read") as mock_pubsub_conn:
        mock_pubsub_conn.return_value = {
            "metadata": [{"product_name": "AGENT_CONFIG"}],
            "config": [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}],
        }
        tracer_flare_sub._get_data_from_connector_and_exec()

    assert len(os.listdir(TRACER_FLARE_DIRECTORY)) == 2
    assert tracer_flare_sub._get_current_request_start() is not None

    # Setting this to 0 minutes so all jobs are considered stale
    tracer_flare_sub._set_stale_tracer_flare_num_mins(0)
    assert tracer_flare_sub._has_stale_flare()

    tracer_flare_sub._get_data_from_connector_and_exec()

    # Everything is cleaned up, reverted, and no current tracer flare
    # timestamp
    assert tracer_flare_sub._get_current_request_start() is None
    confirm_cleanup(get_logger("ddtrace.settings.config"))


def test_tracer_flare_no_overlapping_requests():
    """
    If a new tracer flare request is generated while processing
    a pre-existing request, we will continue processing the current
    one while disregarding the new request(s)
    """
    tracer_flare_sub = TracerFlareSubscriber(
        data_connector=PublisherSubscriberConnector(), callback=config._handle_tracerflare
    )

    # Generate an AGENT_CONFIG product to trigger a request
    with mock.patch("ddtrace.internal.remoteconfig._connectors.PublisherSubscriberConnector.read") as mock_pubsub_conn:
        mock_pubsub_conn.return_value = {
            "metadata": [{"product_name": "AGENT_CONFIG"}],
            "config": [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}],
        }
        tracer_flare_sub._get_data_from_connector_and_exec()

    assert len(os.listdir(TRACER_FLARE_DIRECTORY)) == 2
    original_request_start = tracer_flare_sub._get_current_request_start()
    assert original_request_start is not None

    # Generate another AGENT_CONFIG product to trigger a request
    # This should not be processed
    with mock.patch("ddtrace.internal.remoteconfig._connectors.PublisherSubscriberConnector.read") as mock_pubsub_conn:
        mock_pubsub_conn.return_value = {
            "metadata": [{"product_name": "AGENT_CONFIG"}],
            "config": [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}],
        }
        tracer_flare_sub._get_data_from_connector_and_exec()

    # Nothing should have changed, and we should still be processing
    # only the original request
    assert tracer_flare_sub._get_current_request_start() == original_request_start
    assert len(os.listdir(TRACER_FLARE_DIRECTORY)) == 2
