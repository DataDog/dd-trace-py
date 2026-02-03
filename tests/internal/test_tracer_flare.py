import json
import logging
from logging import Logger
import multiprocessing
import os
import pathlib
import shutil
from typing import Dict
from typing import Optional
from typing import Union
from typing import cast
import unittest
from unittest import mock

from pyfakefs.fake_filesystem_unittest import TestCase
import pytest

from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
from ddtrace.internal.flare.flare import TRACER_FLARE_FILE_HANDLER_NAME
from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.flare.handler import _handle_tracer_flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import native_flare  # type: ignore
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from tests.utils import remote_config_build_payload as build_payload


DEBUG_LEVEL_INT = logging.DEBUG
TRACE_AGENT_URL = "http://localhost:9126"
MOCK_FLARE_SEND_REQUEST = ("1111111", "myhostname", "user.name@datadoghq.com", "d53fc8a4-8820-47a2-aa7d-d565582feb81")


def create_mock_native_manager():
    """Helper to create a mock native manager for tests."""
    mock_manager = mock.MagicMock()

    # Make write_config_file actually write the file
    def write_config_side_effect(file_path, config_json):
        with open(file_path, "w") as f:
            f.write(config_json)

    mock_manager.write_config_file = mock.MagicMock(side_effect=write_config_side_effect)

    mock_manager.zip_and_send = mock.MagicMock()

    # Make cleanup_directory actually remove the directory
    def cleanup_side_effect(directory):
        import shutil

        if os.path.exists(directory):
            shutil.rmtree(directory)

    mock_manager.cleanup_directory = mock.MagicMock(side_effect=cleanup_side_effect)

    return mock_manager


# Helper functions for multiprocessing tests (must be module-level for pickling)
def _multiproc_handle_agent_config(trace_agent_url: str, shared_dir: pathlib.Path, errors: multiprocessing.Queue):
    """Helper for multiprocessing tests - handles AGENT_CONFIG (prepare)."""
    try:
        # Create Flare object inside the process to avoid pickling issues
        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=shared_dir,
            ddconfig={"config": "testconfig"},
        )
        flare.prepare("DEBUG")
        # Assert that each process wrote its file successfully
        if len(os.listdir(shared_dir)) == 0:
            errors.put(Exception("Files were not generated"))
    except Exception as e:
        errors.put(e)


def _multiproc_handle_agent_task(trace_agent_url: str, shared_dir: pathlib.Path, errors: multiprocessing.Queue):
    """Helper for multiprocessing tests - handles AGENT_TASK (send)."""
    try:
        # Create both Flare and data_manager inside the process to avoid pickling issues
        data_manager = native_flare.TracerFlareManager(trace_agent_url, "python")
        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=shared_dir,
            ddconfig={"config": "testconfig"},
        )
        flare.send(setup_task_request(data_manager, *MOCK_FLARE_SEND_REQUEST))
        if os.path.exists(shared_dir):
            errors.put(Exception("Directory was not cleaned up"))
    except Exception as e:
        errors.put(e)


def _multiproc_do_tracer_flare(
    log_level: str,
    case_id: str,
    hostname: str,
    email: str,
    uuid: str,
    trace_agent_url: str,
    shared_dir: pathlib.Path,
    errors: multiprocessing.Queue,
):
    """Helper for multiprocessing partial failure test."""
    try:
        # Create Flare and ReturnAction inside the process to avoid pickling issues
        data_manager = native_flare.TracerFlareManager(trace_agent_url, "python")
        send_request = setup_task_request(data_manager, case_id, hostname, email, uuid)

        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=shared_dir,
            ddconfig={"config": "testconfig"},
        )
        result = flare.prepare(log_level)
        if not result:
            raise Exception(f"Prepare failed with log_level={log_level}")
        # Check that files were generated (at least log + config)
        # Use >= instead of == because other processes might have written files too
        file_count = len(os.listdir(shared_dir))
        if file_count < 2:
            raise Exception(f"Expected at least 2 files, got {file_count}")
        flare.send(send_request)
    except Exception as e:
        errors.put(e)


def setup_task_request(
    manager: native_flare.TracerFlareManager, case_id: str, hostname: str, email: str, uuid: str
) -> native_flare.ReturnAction:
    config = {
        "args": {"case_id": case_id, "hostname": hostname, "user_handle": email},
        "task_type": "tracer_flare",
        "uuid": uuid,
    }
    return manager.handle_remote_config_data(config, "AGENT_TASK")


class TracerFlareTests(TestCase):
    mock_config_dict = {}

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tmp_path, caplog):
        self.tmp_path = tmp_path
        self._caplog = caplog

    def setUp(self):
        # Defensive cleanup: remove any pre-existing tracer flare handlers
        self._remove_handlers()

        self.setUpPyfakefs()
        self.shared_dir = self.fs.create_dir("tracer_flare_test")

        # Real manager for setup_task_request
        self.data_manager = native_flare.TracerFlareManager(TRACE_AGENT_URL, "python")

        # Mock the native manager class before creating Flare object
        self.mock_native_manager = create_mock_native_manager()
        self.native_manager_patcher = mock.patch(
            "ddtrace.internal.flare.flare.native_flare.TracerFlareManager", return_value=self.mock_native_manager
        )
        self.native_manager_patcher.start()

        self.flare = Flare(
            trace_agent_url=TRACE_AGENT_URL,
            flare_dir=pathlib.Path(self.shared_dir.name),
            ddconfig={"config": "testconfig"},
        )
        self.pid = os.getpid()
        self.flare_file_path = f"{self.shared_dir.name}/tracer_python_{self.pid}.log"
        self.config_file_path = f"{self.shared_dir.name}/tracer_config_{self.pid}.json"
        self.prepare_called = False  # Track if prepare() was called

    def tearDown(self):
        # Ensure we always revert configs to clean up handlers
        try:
            self.flare.revert_configs()
        except Exception:
            pass
        self.native_manager_patcher.stop()
        self.confirm_cleanup()

    def _get_handler(self) -> Optional[logging.Handler]:
        ddlogger = get_logger("ddtrace")
        handlers = ddlogger.handlers
        for handler in handlers:
            if handler.name == TRACER_FLARE_FILE_HANDLER_NAME:
                return handler
        return None

    def _remove_handlers(self):
        """Remove all tracer flare file handlers from the ddtrace logger."""
        ddlogger = get_logger("ddtrace")
        for handler in ddlogger.handlers[:]:  # Copy list to avoid modification during iteration
            if handler.name == TRACER_FLARE_FILE_HANDLER_NAME:
                ddlogger.removeHandler(handler)

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

        # Sends request - native manager is already mocked
        self.flare.send(setup_task_request(self.data_manager, *MOCK_FLARE_SEND_REQUEST))

    def test_single_process_partial_failure(self):
        """
        Validate that even if log file creation fails,
        we still attempt to send the flare with partial info (ensure best effort)
        """
        ddlogger = get_logger("ddtrace")
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)

        self.flare.prepare("DEBUG")
        self.prepare_called = True

        file_handler = self._get_handler()
        assert file_handler is not None
        assert file_handler.level == DEBUG_LEVEL_INT
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)

        self.flare.send(setup_task_request(self.data_manager, *MOCK_FLARE_SEND_REQUEST))

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

    def test_json_logs(self):
        """
        Validate that logs produced are in JSON format

        We validate that logs are written as JSON in a specific format
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        ddlogger = get_logger("ddtrace.flare.test.module")
        ddlogger.debug("this is a test log")
        ddlogger.info("this is another test log with a number: %d", 1234)
        ddlogger.warning("this is a warning with a float: %.2f", 12.34)
        try:
            1 / 0
        except ZeroDivisionError:
            ddlogger.exception("this is an exception log")

        assert os.path.exists(self.flare_file_path)

        logs = []
        with open(self.flare_file_path, "r") as file:
            for line in file:
                data = json.loads(line)
                assert isinstance(data, dict), f"Log line is not a JSON object: {line}"
                for key, value in data.items():
                    assert isinstance(key, str), f"Log line has non-string key: {key} in line: {line}"
                    assert value is None or isinstance(value, (str, int, float)), (
                        f"Log line has non-string/int/float/None value: {value} in line: {line}"
                    )

                data = cast(Dict[str, Union[str, int, float, None]], data)

                required_keys = {
                    "filename",
                    "funcName",
                    "level",
                    "lineno",
                    "logger",
                    "message",
                    "module",
                    "process",
                    "processName",
                    "thread",
                    "threadName",
                    "timestamp",
                }
                log_keys = set(data.keys())
                assert required_keys.issubset(log_keys), (
                    f"Log line is missing required keys: {required_keys - log_keys}"
                )
                logs.append(data)

        # Verify the routing message exists
        routing_logs = [log for log in logs if log["message"].startswith("ddtrace logs will be routed to")]
        assert len(routing_logs) == 1, "Expected exactly one routing message log"
        assert routing_logs[0]["logger"] == "ddtrace"
        assert routing_logs[0]["level"] == "DEBUG"

        # Filter to only logs from our test logger
        test_logs = [log for log in logs if log["logger"] == "ddtrace.flare.test.module"]
        assert len(test_logs) == 4, f"Expected 4 test logs, got {len(test_logs)}"

        assert test_logs[0]["level"] == "DEBUG"
        assert test_logs[0]["message"] == "this is a test log"

        assert test_logs[1]["level"] == "INFO"
        assert test_logs[1]["message"] == "this is another test log with a number: 1234"

        assert test_logs[2]["level"] == "WARNING"
        assert test_logs[2]["message"] == "this is a warning with a float: 12.34"

        assert test_logs[3]["level"] == "ERROR"
        assert test_logs[3]["message"] == "this is an exception log"
        assert test_logs[3]["exception"].startswith("Traceback (most recent call last):")
        assert "ZeroDivisionError" in test_logs[3]["exception"]

        self.flare.clean_up_files()
        self.flare.revert_configs()

    @fibonacci_backoff_with_jitter(attempts=10, initial_wait=0.1)
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
        non_numeric_request = setup_task_request(
            self.data_manager,
            case_id="abc123",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        # We can verify this by checking that zip_and_send was not called
        self.flare.send(non_numeric_request)
        # Verify that zip_and_send was not attempted
        self.mock_native_manager.zip_and_send.assert_not_called()

        # Test with empty string case_id
        empty_case_request = setup_task_request(
            self.data_manager,
            case_id="",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # Reset the mock to track this call separately
        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(empty_case_request)
        self.mock_native_manager.zip_and_send.assert_not_called()

        # Test with case_id containing special characters - should work with pattern like "123-456"
        special_char_request = setup_task_request(
            self.data_manager,
            case_id="123-with-debug",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # This should succeed as it matches the pattern \d+-(with-debug|with-content)
        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(special_char_request)
        self.mock_native_manager.zip_and_send.assert_called_once()

        # Test with valid numeric case_id (should work)
        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(valid_request)
        # Verify that zip_and_send was attempted for valid case_id
        self.mock_native_manager.zip_and_send.assert_called_once()

    def test_case_id_cannot_be_zero(self):
        """
        Validate that case_id cannot be 0 or "0"
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with case_id as "0"
        zero_case_request = setup_task_request(
            self.data_manager,
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        self.flare.send(zero_case_request)
        # Verify that zip_and_send was not attempted
        self.mock_native_manager.zip_and_send.assert_not_called()

        # Test with valid non-zero case_id (should work)
        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(valid_request)
        # Verify that zip_and_send was attempted for valid case_id
        self.mock_native_manager.zip_and_send.assert_called_once()

    def test_flare_dir_cleaned_on_all_send_exit_points(self):
        """
        Flare directory should be cleaned up after send, regardless of exit point.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        # Early return: case_id is '0'
        zero_case_request = setup_task_request(
            self.data_manager,
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        assert zero_case_request is not None
        # print zero_case_request to debug
        print(f"zero_case_request: {zero_case_request}")
        self.flare.send(zero_case_request)
        self.mock_native_manager.zip_and_send.assert_not_called()
        assert not self.flare.flare_dir.exists()

        # Success case: valid case_id
        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(valid_request)
        self.mock_native_manager.zip_and_send.assert_called_once()
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

        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        self.flare.send(valid_request)
        self.mock_native_manager.zip_and_send.assert_called_once()
        # Directory should be cleaned up after send
        assert not self.flare.flare_dir.exists()

    def test_flare_dir_cleaned_on_send_error(self):
        """
        Flare directory should be cleaned up if send raises an error.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        # Mock zip_and_send to raise an exception
        self.mock_native_manager.zip_and_send.side_effect = Exception("fail")
        try:
            self.flare.send(valid_request)
        except Exception as exc:
            # Check that this is the Exception raised by zip_and_send
            assert str(exc) == "fail"
        else:
            assert False, "Expected Exception('fail') to be raised"
        # Reset side effect
        self.mock_native_manager.zip_and_send.side_effect = None
        assert not self.flare.flare_dir.exists()

    def test_uuid_field_validation(self):
        """
        Validate that uuid field is properly handled in the ReturnAction
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with valid uuid
        valid_request = setup_task_request(
            self.data_manager,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        self.flare.send(valid_request)
        self.mock_native_manager.zip_and_send.assert_called_once()

        # Test with empty uuid
        empty_uuid_request = setup_task_request(
            self.data_manager, case_id="123456", hostname="myhostname", email="user.name@datadoghq.com", uuid=""
        )

        self.mock_native_manager.zip_and_send.reset_mock()
        self.flare.send(empty_uuid_request)
        self.mock_native_manager.zip_and_send.assert_called_once()

    def test_config_file_contents_validation(self):
        """
        Validate that flare preparation and log file creation works correctly.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Check that the flare directory exists and contains log files
        assert self.flare.flare_dir.exists(), "Flare directory should exist"

        # Check for log files (should be created)
        log_files = list(self.flare.flare_dir.glob("tracer_python_*.log"))
        assert len(log_files) >= 1, "Log files should be created"

        problematic_config = {
            "normal_key": "normal_value",
            "problematic_key": object(),  # Non-serializable object (would fail JSON)
        }

        # Create a new flare instance with problematic config
        problematic_flare = Flare(
            trace_agent_url="http://localhost:8126",
            ddconfig=problematic_config,
            api_key="test_api_key",
            flare_dir="tracer_flare_problematic_test",
        )

        # This should work fine since we don't serialize config anymore
        problematic_flare.prepare("DEBUG")

        # Check that the flare directory still exists and contains log files
        assert problematic_flare.flare_dir.exists(), "Flare directory should exist"

        # Check for log files (should still be created)
        log_files = list(problematic_flare.flare_dir.glob("tracer_python_*.log"))
        assert len(log_files) >= 1, "Log files should be created"

        # Clean up
        problematic_flare.clean_up_files()
        problematic_flare.revert_configs()

        # Clean up original flare
        self.flare.clean_up_files()
        self.flare.revert_configs()


class TracerFlareMultiprocessTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tmp_path):
        self.tmp_path = tmp_path

    def setUp(self):
        self.shared_dir = self.tmp_path / "tracer_flare_test"
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.errors = multiprocessing.Queue()

        # Real manager for setup_task_request
        self.data_manager = native_flare.TracerFlareManager(TRACE_AGENT_URL, "python")

        # Patch the native manager module-wide so it works across processes
        self.native_manager_patcher = mock.patch(
            "ddtrace.internal.flare.flare.native_flare.TracerFlareManager", return_value=create_mock_native_manager()
        )
        self.native_manager_patcher.start()

    def tearDown(self):
        self.native_manager_patcher.stop()

    def test_multiple_process_success(self):
        """
        Validate that the tracer flare will generate for multiple processes
        """
        processes = []
        num_processes = 3

        # Create multiple processes - use module-level function for pickling
        # Flare objects are created inside the process to avoid pickling issues
        for i in range(num_processes):
            p = multiprocessing.Process(
                target=_multiproc_handle_agent_config, args=(TRACE_AGENT_URL, self.shared_dir, self.errors)
            )
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        for i in range(num_processes):
            p = multiprocessing.Process(
                target=_multiproc_handle_agent_task, args=(TRACE_AGENT_URL, self.shared_dir, self.errors)
            )
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        # Check for errors (don't use qsize() as it's not supported on macOS)
        errors_list = []
        while not self.errors.empty():
            try:
                errors_list.append(self.errors.get_nowait())
            except Exception:
                break
        assert len(errors_list) == 0, f"Expected no errors, got: {errors_list}"

    def test_multiple_process_partial_failure(self):
        """
        Validate that even if the tracer flare fails for one process, we should
        still continue the work for the other processes (ensure best effort)
        """
        processes = []

        # Create successful process - use module-level function for pickling
        # Flare objects and ReturnActions are created inside the process to avoid pickling issues
        p = multiprocessing.Process(
            target=_multiproc_do_tracer_flare,
            args=("DEBUG", *MOCK_FLARE_SEND_REQUEST, TRACE_AGENT_URL, self.shared_dir, self.errors),
        )
        processes.append(p)
        p.start()
        # Create failing process
        p = multiprocessing.Process(
            target=_multiproc_do_tracer_flare,
            args=(None, *MOCK_FLARE_SEND_REQUEST, TRACE_AGENT_URL, self.shared_dir, self.errors),
        )
        processes.append(p)
        p.start()
        for p in processes:
            p.join()
        # Check for errors (don't use qsize() as it's not supported on macOS)
        errors_list = []
        while not self.errors.empty():
            try:
                errors_list.append(self.errors.get_nowait())
            except Exception:
                break
        assert len(errors_list) == 1, f"Expected 1 error, got {len(errors_list)}: {errors_list}"


class MockPubSubConnector(PublisherSubscriberConnector):
    def __init__(self):
        pass

    def read(self):
        pass

    def write(self):
        pass


class TracerFlareSubscriberTests(unittest.TestCase):
    agent_config = {"name": "flare-log-level.test", "config": {"log_level": "DEBUG"}}
    agent_task = {
        "args": {
            "case_id": "1111111",
            "hostname": "myhostname",
            "user_handle": "user.name@datadoghq.com",
        },
        "task_type": "tracer_flare",
        "uuid": "d53fc8a4-8820-47a2-aa7d-d565582feb81",
    }

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tmp_path):
        self.tmp_path = tmp_path

    def setUp(self):
        self.shared_dir = self.tmp_path / "tracer_flare_test"
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.tracer_flare_sub = TracerFlareSubscriber(
            data_connector=MockPubSubConnector(),
            callback=_handle_tracer_flare,
            flare=Flare(
                trace_agent_url=TRACE_AGENT_URL,
                ddconfig={"config": "testconfig"},
                flare_dir=self.shared_dir,
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
        Ensure a successful tracer flare process
        """
        assert self.tracer_flare_sub.stale_tracer_flare_num_mins == 20

        # Generate an AGENT_CONFIG product to start the flare request
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            # prepare should be called with the log level from config (lowercase from libdatadog)
            mock_flare_prep.assert_called_once_with("debug")

        # Generate an AGENT_TASK product to complete the request
        with mock.patch("ddtrace.internal.flare.flare.Flare.send") as mock_flare_send:
            self.generate_agent_task()
            mock_flare_send.assert_called_once()

        # Timestamp cleared after request completed
        assert self.tracer_flare_sub.current_request_start is None, (
            "current_request_start timestamp should have been reset after request was completed"
        )

    def test_detect_stale_flare(self):
        """
        Ensure we clean up and revert configurations if a tracer
        flare job has gone stale
        """
        # Set this to 0 so all requests are stale
        self.tracer_flare_sub.stale_tracer_flare_num_mins = 0

        # Start a flare request with AGENT_CONFIG
        self.generate_agent_config()
        assert self.tracer_flare_sub.current_request_start is not None

        # Setting this to 0 minutes so all jobs are considered stale
        assert self.tracer_flare_sub.has_stale_flare()

        # Trigger cleanup of stale request by receiving another AGENT_CONFIG
        with mock.patch("ddtrace.internal.flare.flare.Flare.revert_configs") as mock_revert:
            with mock.patch("ddtrace.internal.flare.flare.Flare.clean_up_files") as mock_cleanup:
                self.tracer_flare_sub._get_data_from_connector_and_exec()
                # The subscriber should detect staleness and clean up
                mock_revert.assert_called_once()
                mock_cleanup.assert_called_once()

        # After handling stale request, state should be reset
        assert self.tracer_flare_sub.current_request_start is None, "current_request_start should have been reset"

    def test_no_overlapping_requests(self):
        """
        If a new tracer flare request is generated while processing
        a pre-existing request, we will continue processing the current
        one while disregarding the new request(s)
        """
        # Start initial flare request
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            mock_flare_prep.return_value = True
            self.generate_agent_config()
            # prepare should be called for the first request (lowercase from libdatadog)
            mock_flare_prep.assert_called_once_with("debug")

        original_request_start = self.tracer_flare_sub.current_request_start
        assert original_request_start is not None

        # Generate another AGENT_CONFIG while first one is still active
        # libdatadog v26 will return None since already collecting,
        # and subscriber will ignore it
        with mock.patch("ddtrace.internal.flare.flare.Flare.prepare") as mock_flare_prep:
            self.generate_agent_config()
            # prepare should not be called because there's already an active request
            mock_flare_prep.assert_not_called()

        assert self.tracer_flare_sub.current_request_start == original_request_start, (
            "Original request should not have been updated with newer request start time"
        )


def test_native_logs(tmp_path):
    """
    Validate that the flare collects native logs if native writer is enabled.
    The native logs cannot be collected with Pyfakefs so we use tmp_path.
    """
    import os

    from ddtrace import config
    from ddtrace.internal.native._native import logger as native_logger

    original_trace_writer_native = config._trace_writer_native
    flare = None
    try:
        config._trace_writer_native = True
        flare = Flare(
            trace_agent_url=TRACE_AGENT_URL,
            flare_dir=tmp_path,
            ddconfig={"config": "testconfig"},
        )

        flare.prepare("DEBUG")

        native_logger.log("debug", "debug log")
        native_logger.disable("file")  # Flush the non-blocking writer

        native_flare_file_path = tmp_path / f"tracer_native_{os.getpid()}.log"
        assert os.path.exists(native_flare_file_path)

        with open(native_flare_file_path, "r") as file:
            assert "debug log" in file.readline()

        # Sends request to testagent
        # This just validates the request params
        data_manager = native_flare.TracerFlareManager(TRACE_AGENT_URL, "python")
        send_request = setup_task_request(data_manager, *MOCK_FLARE_SEND_REQUEST)
        flare.send(send_request)
    finally:
        config._trace_writer_native = original_trace_writer_native
        if flare is not None:
            flare.revert_configs()
            flare.clean_up_files()
