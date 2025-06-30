import json
import logging
from logging import Logger
import multiprocessing
import os
import pathlib
import re
import shutil
from typing import Optional
from unittest import mock

from pyfakefs.fake_filesystem_unittest import TestCase
import pytest

from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
from ddtrace.internal.flare.flare import TRACER_FLARE_FILE_HANDLER_NAME
from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.flare import FlareSendRequest
from ddtrace.internal.flare.handler import _handle_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from tests.utils import remote_config_build_payload as build_payload


DEBUG_LEVEL_INT = logging.DEBUG
TRACE_AGENT_URL = "http://localhost:9126"
MOCK_FLARE_SEND_REQUEST = FlareSendRequest(
    case_id="1111111",
    hostname="myhostname",
    email="user.name@datadoghq.com",
    uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
)


class TracerFlareTests(TestCase):
    mock_config_dict = {}

    def setUp(self):
        self.setUpPyfakefs()
        self.shared_dir = self.fs.create_dir("tracer_flare_test")
        self.flare = Flare(
            trace_agent_url=TRACE_AGENT_URL,
            flare_dir=pathlib.Path(self.shared_dir.name),
            ddconfig={"config": "testconfig"},
        )
        self.pid = os.getpid()
        self.flare_file_path = f"{self.shared_dir.name}/tracer_python_{self.pid}.log"
        self.config_file_path = f"{self.shared_dir.name}/tracer_config_{self.pid}.json"
        self.prepare_called = False  # Track if prepare() was called

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def tearDown(self):
        self.confirm_cleanup()

    def _get_handler(self) -> Optional[logging.Handler]:
        ddlogger = get_logger("ddtrace")
        handlers = ddlogger.handlers
        for handler in handlers:
            if handler.name == TRACER_FLARE_FILE_HANDLER_NAME:
                return handler
        return None

    def test_single_process_success(self):
        """
        Validate that the baseline tracer flare works for a single process
        """
        ddlogger = get_logger("ddtrace")

        self.flare.prepare("DEBUG")
        self.prepare_called = True

        file_handler = self._get_handler()
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)
        assert file_handler is not None, "File handler did not get added to the ddtrace logger"
        assert file_handler.level == DEBUG_LEVEL_INT, "File handler does not have the correct log level"
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert os.path.exists(self.config_file_path)

        # Sends request to testagent
        # This just validates the request params
        self.flare.send(MOCK_FLARE_SEND_REQUEST)

    def test_single_process_partial_failure(self):
        """
        Validate that even if one of the files fails to be generated,
        we still attempt to send the flare with partial info (ensure best effort)
        """
        ddlogger = get_logger("ddtrace")
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)

        # Mock the partial failure
        with mock.patch("json.dump") as mock_json:
            mock_json.side_effect = Exception("this is an expected error")
            self.flare.prepare("DEBUG")
            self.prepare_called = True

        file_handler = self._get_handler()
        assert file_handler is not None
        assert file_handler.level == DEBUG_LEVEL_INT
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert not os.path.exists(self.config_file_path)

        self.flare.send(MOCK_FLARE_SEND_REQUEST)

    def test_no_app_logs(self):
        """
        Validate that app logs are not being added to the
        file, just the tracer logs
        """
        app_logger = Logger(name="my-app", level=DEBUG_LEVEL_INT)
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        app_log_line = "this is an app log"
        app_logger.debug(app_log_line)

        assert os.path.exists(self.flare_file_path)

        with open(self.flare_file_path, "r") as file:
            for line in file:
                assert app_log_line not in line, f"File {self.flare_file_path} contains excluded line: {app_log_line}"

        self.flare.clean_up_files()
        self.flare.revert_configs()

    def confirm_cleanup(self):
        assert not self.flare.flare_dir.exists(), f"The directory {self.flare.flare_dir} still exists"
        # Only check for file handler cleanup if prepare() was called
        if self.prepare_called:
            assert self._get_handler() is None, "File handler was not removed"

    def test_case_id_must_be_numeric(self):
        """
        Validate that case_id must be numeric (contain only digits)
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with non-numeric case_id
        non_numeric_request = FlareSendRequest(
            case_id="abc123",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        # We can verify this by checking that no HTTP request is made
        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            self.flare.send(non_numeric_request)
            # Verify that no HTTP connection was attempted
            mock_connection.assert_not_called()

        # Test with empty string case_id
        empty_case_request = FlareSendRequest(
            case_id="",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            self.flare.send(empty_case_request)
            mock_connection.assert_not_called()

        # Test with case_id containing special characters
        special_char_request = FlareSendRequest(
            case_id="123-456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            self.flare.send(special_char_request)
            mock_connection.assert_not_called()

        # Test with valid numeric case_id (should work)
        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            # Mock a successful response
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client

            self.flare.send(valid_request)
            # Verify that HTTP connection was attempted for valid case_id
            mock_connection.assert_called_once()

    def test_case_id_cannot_be_zero(self):
        """
        Validate that case_id cannot be 0 or "0"
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with case_id as "0"
        zero_case_request = FlareSendRequest(
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            self.flare.send(zero_case_request)
            # Verify that no HTTP connection was attempted
            mock_connection.assert_not_called()

        # Test with valid non-zero case_id (should work)
        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            # Mock a successful response
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client

            self.flare.send(valid_request)
            # Verify that HTTP connection was attempted for valid case_id
            mock_connection.assert_called_once()

    def test_flare_dir_cleaned_on_all_send_exit_points(self):
        """
        Flare directory should be cleaned up after send, regardless of exit point.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        # Early return: case_id is '0'
        zero_case_request = FlareSendRequest(
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            self.flare.send(zero_case_request)
            mock_connection.assert_not_called()
        assert not self.flare.flare_dir.exists()

        # Success case: valid case_id
        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client
            self.flare.send(valid_request)
            mock_connection.assert_called_once()
        assert not self.flare.flare_dir.exists()

    def test_prepare_creates_flare_dir(self):
        """
        Prepare should create the flare directory if it doesn't exist.
        """
        # Remove directory if it exists
        if self.flare.flare_dir.exists():
            shutil.rmtree(self.flare.flare_dir)

        # Call prepare - should create the directory
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        assert self.flare.flare_dir.exists()

        # Clean up manually since prepare doesn't call clean_up_files
        self.flare.clean_up_files()
        # Also revert configs to remove the file handler
        self.flare.revert_configs()

    def test_send_creates_flare_dir_if_missing(self):
        """
        Send should create the flare directory if it doesn't exist and then clean it up.
        """
        # Remove directory if it exists
        if self.flare.flare_dir.exists():
            shutil.rmtree(self.flare.flare_dir)

        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client
            self.flare.send(valid_request)
            mock_connection.assert_called_once()
        # Directory should be cleaned up after send
        assert not self.flare.flare_dir.exists()

    def test_flare_dir_cleaned_on_send_error(self):
        """
        Flare directory should be cleaned up if send raises an error.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        with mock.patch("ddtrace.internal.flare.flare.get_connection", side_effect=Exception("fail")):
            try:
                self.flare.send(valid_request)
            except Exception as exc:
                # Check that this is the Exception raised in _execute_mock_call and no other one
                assert str(exc) == "fail"
            else:
                assert False, "Expected Exception('fail') to be raised"
        assert not self.flare.flare_dir.exists()

    def test_uuid_field_validation(self):
        """
        Validate that uuid field is properly handled in FlareSendRequest
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with valid uuid
        valid_request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client
            self.flare.send(valid_request)
            mock_connection.assert_called_once()

        # Test with empty uuid
        empty_uuid_request = FlareSendRequest(
            case_id="123456", hostname="myhostname", email="user.name@datadoghq.com", uuid=""
        )

        with mock.patch("ddtrace.internal.flare.flare.get_connection") as mock_connection:
            mock_client = mock.MagicMock()
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_client.getresponse.return_value = mock_response
            mock_connection.return_value = mock_client
            self.flare.send(empty_uuid_request)
            mock_connection.assert_called_once()

    def test_uuid_in_payload(self):
        """
        Validate that uuid field is included in the generated payload
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        test_uuid = "d53fc8a4-8820-47a2-aa7d-d565582feb81"
        request = FlareSendRequest(
            case_id="123456", hostname="myhostname", email="user.name@datadoghq.com", uuid=test_uuid
        )

        _, body = self.flare._generate_payload(request)

        try:
            body_str = body.decode("utf-8")
        except UnicodeDecodeError:
            body_str = body[:1000].decode("utf-8", errors="ignore")

        assert test_uuid in body_str, f"UUID {test_uuid} should be in payload form fields"
        self.flare.clean_up_files()
        self.flare.revert_configs()

    def test_config_file_contents_validation(self):
        """
        Validate that config file contents are properly checked and logged when generation fails
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with valid config - should generate config file
        FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # Check that config file was created with proper contents
        config_files = list(self.flare.flare_dir.glob("tracer_config_*.json"))
        assert len(config_files) == 1, "Should have exactly one config file"

        config_file = config_files[0]
        with open(config_file, "r") as f:
            config_data = json.load(f)

        # Validate config structure
        assert "configs" in config_data, "Config should have 'configs' key"
        assert config_data["configs"] == self.flare.ddconfig, "Config should contain ddconfig"

        # Test with problematic ddconfig that might cause JSON serialization issues
        problematic_config = {
            "normal_key": "normal_value",
            "problematic_key": object(),  # Non-serializable object
        }

        # Create a new flare instance with problematic config
        problematic_flare = Flare(
            trace_agent_url="http://localhost:8126",
            ddconfig=problematic_config,
            api_key="test_api_key",
            flare_dir="tracer_flare_problematic_test",
        )

        # This should handle the serialization error gracefully
        problematic_flare.prepare("DEBUG")

        # Check that the flare directory still exists and contains log files
        assert problematic_flare.flare_dir.exists(), "Flare directory should exist even if config generation fails"

        # Check for log files (should still be created)
        log_files = list(problematic_flare.flare_dir.glob("tracer_python_*.log"))
        assert len(log_files) >= 1, "Log files should still be created even if config generation fails"

        # Clean up
        problematic_flare.clean_up_files()
        problematic_flare.revert_configs()

        # Clean up original flare
        self.flare.clean_up_files()
        self.flare.revert_configs()

    def test_api_key_not_in_payload(self):
        """
        Validate that DD-API-KEY header is not included in the payload since the agent forwards it
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # Generate payload and check headers
        headers, body = self.flare._generate_payload(request)

        # Verify that DD-API-KEY is not in the headers
        assert "DD-API-KEY" not in headers, "DD-API-KEY should not be in headers - agent forwards it"
        assert "dd-api-key" not in headers, "dd-api-key should not be in headers - agent forwards it"

        # Verify that the API key is not in the body content
        body_str = body[:1000].decode("utf-8", errors="ignore")
        api_key = self.flare._api_key
        if api_key:
            assert api_key not in body_str, "API key should not be in payload body - agent forwards it"

        # Check that API key is redacted in the config file
        config_files = list(self.flare.flare_dir.glob("tracer_config_*.json"))
        assert len(config_files) == 1, "Should have exactly one config file"

        config_file = config_files[0]
        with open(config_file, "r") as f:
            config_data = json.load(f)

        # Check that _dd_api_key is redacted (should be ****last4chars format)
        if "_dd_api_key" in config_data["configs"]:
            redacted_key = config_data["configs"]["_dd_api_key"]
            assert redacted_key.startswith("*" * (len(api_key) - 4)), "API key should be redacted with asterisks"
            assert redacted_key.endswith(api_key[-4:]), "API key should end with last 4 characters"
            assert redacted_key != api_key, "API key should not be the original value"

        # Clean up
        self.flare.clean_up_files()
        self.flare.revert_configs()

    def test_payload_field_order(self):
        """
        Validate that the multipart form-data payload fields are in the correct order:
        source, case_id, hostname, email, uuid, flare_file
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        request = FlareSendRequest(
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # Generate payload
        headers, body = self.flare._generate_payload(request)

        # Convert body to string for easier parsing
        body_str = body.decode("utf-8", errors="ignore")

        # Find all Content-Disposition lines to extract field order
        content_disposition_pattern = r'Content-Disposition: form-data; name="([^"]+)"'
        field_names = re.findall(content_disposition_pattern, body_str)

        # Expected order: source, case_id, hostname, email, uuid, flare_file
        expected_order = ["source", "case_id", "hostname", "email", "uuid", "flare_file"]

        # Verify the order matches exactly
        assert field_names == expected_order, f"Field order mismatch. Expected: {expected_order}, Got: {field_names}"

        # Clean up
        self.flare.clean_up_files()
        self.flare.revert_configs()


class TracerFlareMultiprocessTests(TestCase):
    def setUp(self):
        self.setUpPyfakefs()
        self.shared_dir = self.fs.create_dir("tracer_flare_test")
        self.errors = multiprocessing.Queue()

    def test_multiple_process_success(self):
        """
        Validate that the tracer flare will generate for multiple processes
        """
        processes = []
        num_processes = 3
        flares = []
        for _ in range(num_processes):
            flares.append(
                Flare(
                    trace_agent_url=TRACE_AGENT_URL,
                    flare_dir=pathlib.Path(self.shared_dir.name),
                    ddconfig={"config": "testconfig"},
                )
            )

        def handle_agent_config(flare: Flare):
            try:
                flare.prepare("DEBUG")
                # Assert that each process wrote its file successfully
                # We double the process number because each will generate a log file and a config file
                if len(os.listdir(self.shared_dir.name)) == 0:
                    self.errors.put(Exception("Files were not generated"))
            except Exception as e:
                self.errors.put(e)

        def handle_agent_task(flare: Flare):
            try:
                flare.send(MOCK_FLARE_SEND_REQUEST)
                if os.path.exists(self.shared_dir.name):
                    self.errors.put(Exception("Directory was not cleaned up"))
            except Exception as e:
                self.errors.put(e)

        # Create multiple processes
        for i in range(num_processes):
            flare = flares[i]
            p = multiprocessing.Process(target=handle_agent_config, args=(flare,))
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        for i in range(num_processes):
            flare = flares[i]
            p = multiprocessing.Process(target=handle_agent_task, args=(flare,))
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        assert self.errors.qsize() == 0

    def test_multiple_process_partial_failure(self):
        """
        Validate that even if the tracer flare fails for one process, we should
        still continue the work for the other processes (ensure best effort)
        """
        processes = []
        flares = []
        for _ in range(2):
            flares.append(
                Flare(
                    trace_agent_url=TRACE_AGENT_URL,
                    flare_dir=pathlib.Path(self.shared_dir.name),
                    ddconfig={"config": "testconfig"},
                )
            )

        def do_tracer_flare(log_level: str, send_request: FlareSendRequest, flare: Flare):
            try:
                flare.prepare(log_level)
                # Assert that only one process wrote its file successfully
                # We check for 2 files because it will generate a log file and a config file
                assert 2 == len(os.listdir(self.shared_dir.name))
                flare.send(send_request)
            except Exception as e:
                self.errors.put(e)

        # Create successful process
        p = multiprocessing.Process(target=do_tracer_flare, args=("DEBUG", MOCK_FLARE_SEND_REQUEST, flares[0]))
        processes.append(p)
        p.start()
        # Create failing process
        p = multiprocessing.Process(target=do_tracer_flare, args=(None, MOCK_FLARE_SEND_REQUEST, flares[1]))
        processes.append(p)
        p.start()
        for p in processes:
            p.join()
        assert self.errors.qsize() == 1


class MockPubSubConnector(PublisherSubscriberConnector):
    def __init__(self):
        pass

    def read(self):
        pass

    def write(self):
        pass


class TracerFlareSubscriberTests(TestCase):
    agent_config = [False, {"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
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

    def setUp(self):
        self.setUpPyfakefs()
        self.shared_dir = self.fs.create_dir("tracer_flare_test")
        self.tracer_flare_sub = TracerFlareSubscriber(
            data_connector=MockPubSubConnector(),
            callback=_handle_tracer_flare,
            flare=Flare(
                trace_agent_url=TRACE_AGENT_URL,
                ddconfig={"config": "testconfig"},
                flare_dir=pathlib.Path(self.shared_dir.name),
            ),
        )

    def generate_agent_config(self):
        with mock.patch("tests.internal.test_tracer_flare.MockPubSubConnector.read") as mock_pubsub_conn:
            mock_pubsub_conn.return_value = [build_payload("AGENT_CONFIG", self.agent_config, "config")]
            self.tracer_flare_sub._get_data_from_connector_and_exec()

    def generate_agent_task(self):
        with mock.patch("tests.internal.test_tracer_flare.MockPubSubConnector.read") as mock_pubsub_conn:
            mock_pubsub_conn.return_value = [build_payload("AGENT_TASK", self.agent_task, "task")]
            self.tracer_flare_sub._get_data_from_connector_and_exec()

    def test_process_flare_request_success(self):
        """
        Ensure a full successful tracer flare process
        """
        assert self.tracer_flare_sub.stale_tracer_flare_num_mins == 20
        # Generate an AGENT_CONFIG product to trigger a request
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            mock_flare_prep.assert_called_once()

        assert (
            self.tracer_flare_sub.current_request_start is not None
        ), "current_request_start should be a non-None value after request is received"

        # Generate an AGENT_TASK product to complete the request
        with mock.patch("ddtrace.internal.flare.flare.Flare.send") as mock_flare_send:
            self.generate_agent_task()
            mock_flare_send.assert_called_once()

        # Timestamp cleared after request completed
        assert (
            self.tracer_flare_sub.current_request_start is None
        ), "current_request_start timestamp should have been reset after request was completed"

    def test_detect_stale_flare(self):
        """
        Ensure we clean up and revert configurations if a tracer
        flare job has gone stale
        """
        # Set this to 0 so all requests are stale
        self.tracer_flare_sub.stale_tracer_flare_num_mins = 0

        # Generate an AGENT_CONFIG product to trigger a request
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            mock_flare_prep.assert_called_once()

        assert self.tracer_flare_sub.current_request_start is not None

        # Setting this to 0 minutes so all jobs are considered stale
        assert self.tracer_flare_sub.has_stale_flare()

        self.generate_agent_config()

        assert self.tracer_flare_sub.current_request_start is None, "current_request_start should have been reset"

    def test_no_overlapping_requests(self):
        """
        If a new tracer flare request is generated while processing
        a pre-existing request, we will continue processing the current
        one while disregarding the new request(s)
        """
        # Generate initial AGENT_CONFIG product to trigger a request
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            mock_flare_prep.assert_called_once()

        original_request_start = self.tracer_flare_sub.current_request_start
        assert original_request_start is not None

        # Generate another AGENT_CONFIG product to trigger a request
        # This should not be processed
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            mock_flare_prep.assert_not_called()

        assert (
            self.tracer_flare_sub.current_request_start == original_request_start
        ), "Original request should not have been updated with newer request start time"
