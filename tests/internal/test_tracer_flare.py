import json
import logging
from logging import Logger
import multiprocessing
import os
import pathlib
import shutil
import time
from typing import Optional
from typing import Union
from typing import cast
import unittest
from uuid import uuid4

import pytest

from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
from ddtrace.internal.flare.flare import TRACER_FLARE_FILE_HANDLER_NAME
from ddtrace.internal.flare.flare import Flare
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import native_flare  # type: ignore
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter

# Renamed to avoid pytest trying to collect it as a Test class:
from tests.utils import TestAgentClient as _TestAgentClient
from tests.utils import remote_config_build_payload as build_payload


DEBUG_LEVEL_INT = logging.DEBUG
TRACE_AGENT_URL = "http://localhost:9126"
FLARE_REQUEST_DATA = ("1111111", "myhostname", "user.name@datadoghq.com", "d53fc8a4-8820-47a2-aa7d-d565582feb81")


# Helper functions for multiprocessing tests (must be module-level for pickling)
def _multiproc_handle_agent_config(trace_agent_url: str, shared_dir: pathlib.Path, errors: multiprocessing.Queue):
    """Helper for multiprocessing tests - handles AGENT_CONFIG (prepare)."""
    try:
        # Create Flare object inside the process to avoid pickling issues
        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=str(shared_dir),
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
        # Create Flare inside the process to avoid pickling issues
        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=str(shared_dir),
            ddconfig={"config": "testconfig"},
        )
        flare.send(setup_task_request(flare, *FLARE_REQUEST_DATA))
        # In multiprocess mode, workers share the same directory and may overlap:
        # one worker can recreate the directory while another is still checking it.
        # Avoid asserting per-process cleanup to keep this test deterministic.
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
        # Create Flare and FlareAction inside the process to avoid pickling issues
        flare = Flare(
            trace_agent_url=trace_agent_url,
            flare_dir=str(shared_dir),
            ddconfig={"config": "testconfig"},
        )
        send_request = setup_task_request(flare, case_id, hostname, email, uuid)

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
    flare: Flare, case_id: str, hostname: str, email: str, uuid: str
) -> native_flare.FlareAction:
    config = {
        "args": {"case_id": case_id, "hostname": hostname, "user_handle": email},
        "task_type": "tracer_flare",
        "uuid": uuid,
    }
    return flare.handle_remote_config_data(config, "AGENT_TASK")


class TracerFlareTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tmp_path, caplog):
        self.tmp_path = tmp_path
        self._caplog = caplog

    def setUp(self):
        # Defensive cleanup: remove any pre-existing tracer flare handlers
        self._remove_handlers()

        self.shared_dir = self.tmp_path / "tracer_flare_test"
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        self.flare = Flare(
            trace_agent_url=TRACE_AGENT_URL,
            flare_dir=str(self.shared_dir),
            ddconfig={"config": "testconfig"},
        )
        self.testagent_token = f"tracer-flare-{uuid4().hex}"
        self.testagent_client = _TestAgentClient(base_url=TRACE_AGENT_URL, token=self.testagent_token)
        status, _ = self.testagent_client._request("GET", self.testagent_client._url("/test/session/start"))
        if status == 200:
            self.testagent_client.clear()
        self.pid = os.getpid()
        self.flare_file_path = str(self.shared_dir / f"tracer_python_{self.pid}.log")
        self.config_file_path = str(self.shared_dir / f"tracer_config_{self.pid}.json")
        self.prepare_called = False  # Track if prepare() was called

    def tearDown(self):
        # Ensure we always revert configs to clean up handlers
        try:
            self.flare.revert_configs()
        except Exception:
            pass
        self.confirm_cleanup()

    def _flare_upload_count(self) -> int:
        status, body = self.testagent_client._request("GET", self.testagent_client._url("/test/session/requests"))
        assert status == 200
        requests = json.loads(body)
        return sum(1 for req in requests if (req.get("url") or "").endswith("/tracer_flare/v1"))

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

        self.flare.send(setup_task_request(self.flare, *FLARE_REQUEST_DATA))

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

        self.flare.send(setup_task_request(self.flare, *FLARE_REQUEST_DATA))

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
            _ = 1 / 0
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

                data = cast(dict[str, Union[str, int, float, None]], data)

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

    @pytest.mark.xfail(
        reason="The case of case_id being empty is not handled in the v27.0.0 of libdatadog, but is handled in the v28.0.0+ versions. We should remove this xfail once we update to v28.0.0+ in dd-trace-py.",
        strict=True
    )
    def test_case_id_must_be_numeric(self):
        """
        Validate that case_id must be numeric (contain only digits)
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with non-numeric case_id
        non_numeric_request = setup_task_request(
            self.flare,
            case_id="abc123",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        # We can verify this by checking that zip_and_send was not called
        uploads_before = self._flare_upload_count()
        self.flare.send(non_numeric_request)
        # Verify that zip_and_send was not attempted
        assert self._flare_upload_count() == uploads_before

        # Need to prepare again since the previous send would have cleaned up the flare dir and handlers
        self.flare.revert_configs()
        self.flare.prepare("DEBUG")

        # Test with case_id containing special characters - should work with pattern like "123-456"
        special_char_request = setup_task_request(
            self.flare,
            case_id="123-with-debug",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # This should succeed as it matches the pattern \d+-(with-debug|with-content)
        uploads_before = self._flare_upload_count()
        self.flare.send(special_char_request)
        assert self._flare_upload_count() == uploads_before + 1

        # Need to prepare again since the previous send would have cleaned up the flare dir and handlers
        self.flare.revert_configs()
        self.flare.prepare("DEBUG")

        # Test with valid numeric case_id (should work)
        valid_request = setup_task_request(
            self.flare,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        uploads_before = self._flare_upload_count()
        self.flare.send(valid_request)
        # Verify that zip_and_send was attempted for valid case_id
        assert self._flare_upload_count() == uploads_before + 1

        # Test with empty string case_id
        empty_case_request = setup_task_request(
            self.flare,
            case_id="",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        uploads_before = self._flare_upload_count()
        self.flare.send(empty_case_request)
        assert self._flare_upload_count() == uploads_before

    def test_case_id_cannot_be_zero(self):
        """
        Validate that case_id cannot be 0 or "0"
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with case_id as "0"
        zero_case_request = setup_task_request(
            self.flare,
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        # The send method should return early without sending the flare
        uploads_before = self._flare_upload_count()
        self.flare.send(zero_case_request)
        # Verify that zip_and_send was not attempted
        assert self._flare_upload_count() == uploads_before

        # Need to prepare again since the previous send would have cleaned up the flare dir and handlers
        self.flare.revert_configs()
        self.flare.prepare("DEBUG")

        # Test with valid non-zero case_id (should work)
        valid_request = setup_task_request(
            self.flare,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        uploads_before = self._flare_upload_count()
        self.flare.send(valid_request)
        # Verify that zip_and_send was attempted for valid case_id
        assert self._flare_upload_count() == uploads_before + 1

    def test_flare_dir_cleaned_on_all_send_exit_points(self):
        """
        Flare directory should be cleaned up after send, regardless of exit point.
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True
        # Early return: case_id is '0'
        zero_case_request = setup_task_request(
            self.flare,
            case_id="0",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        assert zero_case_request is not None
        # print zero_case_request to debug
        print(f"zero_case_request: {zero_case_request}")
        uploads_before = self._flare_upload_count()
        self.flare.send(zero_case_request)
        assert self._flare_upload_count() == uploads_before
        assert not self.flare.flare_dir.exists()

        # Need to prepare again since the previous send would have cleaned up the flare dir and handlers
        self.flare.revert_configs()
        self.flare.prepare("DEBUG")

        # Success case: valid case_id
        valid_request = setup_task_request(
            self.flare,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        uploads_before = self._flare_upload_count()
        self.flare.send(valid_request)
        assert self._flare_upload_count() == uploads_before + 1
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

    def test_flare_dir_cleaned_on_send_error(self):
        """
        Flare directory should be cleaned up if send raises an error.
        """
        self.flare.url = "http://localhost:1"
        self.flare._native_manager = native_flare.TracerFlareManager(agent_url=self.flare.url)
        valid_request = setup_task_request(
            self.flare,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )
        try:
            self.flare.send(valid_request)
        except Exception as exc:
            assert "error" in str(exc).lower()
        else:
            assert False, "Expected send to fail for unreachable test agent"
        assert not self.flare.flare_dir.exists()

    def test_uuid_field_validation(self):
        """
        Validate that uuid field is properly handled in the FlareAction
        """
        self.flare.prepare("DEBUG")
        self.prepare_called = True

        # Test with valid uuid
        valid_request = setup_task_request(
            self.flare,
            case_id="123456",
            hostname="myhostname",
            email="user.name@datadoghq.com",
            uuid="d53fc8a4-8820-47a2-aa7d-d565582feb81",
        )

        uploads_before = self._flare_upload_count()
        self.flare.send(valid_request)
        assert self._flare_upload_count() == uploads_before + 1

        self.flare.revert_configs()
        self.flare.prepare("DEBUG")

        # Test with empty uuid
        empty_uuid_request = setup_task_request(
            self.flare, case_id="123456", hostname="myhostname", email="user.name@datadoghq.com", uuid=""
        )

        uploads_before = self._flare_upload_count()
        self.flare.send(empty_uuid_request)
        assert self._flare_upload_count() == uploads_before + 1

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
        # Shared directory cleanup can race across workers; assert eventual cleanup.
        for _ in range(20):
            if not self.shared_dir.exists():
                break
            time.sleep(0.05)
        assert not self.shared_dir.exists(), f"Expected shared dir to be cleaned up: {self.shared_dir}"

    def test_multiple_process_partial_failure(self):
        """
        Validate that even if the tracer flare fails for one process, we should
        still continue the work for the other processes (ensure best effort)
        """
        processes = []

        # Create successful process - use module-level function for pickling
        # Flare objects and FlareActions are created inside the process to avoid pickling issues
        p = multiprocessing.Process(
            target=_multiproc_do_tracer_flare,
            args=("DEBUG", *FLARE_REQUEST_DATA, TRACE_AGENT_URL, self.shared_dir, self.errors),
        )
        processes.append(p)
        p.start()
        # Create failing process
        p = multiprocessing.Process(
            target=_multiproc_do_tracer_flare,
            args=(None, *FLARE_REQUEST_DATA, TRACE_AGENT_URL, self.shared_dir, self.errors),
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
        self.testagent_token = f"tracer-flare-sub-{uuid4().hex}"
        self.testagent_client = _TestAgentClient(base_url=TRACE_AGENT_URL, token=self.testagent_token)
        status, _ = self.testagent_client._request("GET", self.testagent_client._url("/test/session/start"))
        if status == 200:
            self.testagent_client.clear()
        self.connector = PublisherSubscriberConnector()
        self.tracer_flare_sub = TracerFlareSubscriber(
            data_connector=self.connector,
            flare=Flare(
                trace_agent_url=TRACE_AGENT_URL,
                ddconfig={"config": "testconfig"},
                flare_dir=self.shared_dir,
            ),
        )

    def generate_agent_config(self):
        self.connector.write([build_payload("AGENT_CONFIG", self.agent_config, "config")])
        self.tracer_flare_sub._get_data_from_connector_and_exec()

    def generate_agent_task(self):
        self.connector.write([build_payload("AGENT_TASK", self.agent_task, "task")])
        self.tracer_flare_sub._get_data_from_connector_and_exec()

    def _flare_upload_count(self) -> int:
        status, body = self.testagent_client._request("GET", self.testagent_client._url("/test/session/requests"))
        assert status == 200
        requests = json.loads(body)
        return sum(1 for req in requests if (req.get("url") or "").endswith("/tracer_flare/v1"))

    def test_process_flare_request_success(self):
        """
        Ensure a successful tracer flare process
        """
        assert self.tracer_flare_sub.stale_tracer_flare_num_mins == 20

        # Generate an AGENT_CONFIG product to start the flare request
        self.generate_agent_config()
        assert self.tracer_flare_sub.current_request_start is not None
        uploads_before = self._flare_upload_count()

        # Generate an AGENT_TASK product to complete the request
        self.generate_agent_task()
        assert self._flare_upload_count() == uploads_before + 1

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
        self.tracer_flare_sub._get_data_from_connector_and_exec()

        # After handling stale request, state should be reset
        assert self.tracer_flare_sub.current_request_start is None, "current_request_start should have been reset"
        assert not self.tracer_flare_sub.flare.flare_dir.exists()

    def test_no_overlapping_requests(self):
        """
        If a new tracer flare request is generated while processing
        a pre-existing request, we will continue processing the current
        one while disregarding the new request(s)
        """
        # Start initial flare request
        self.generate_agent_config()

        original_request_start = self.tracer_flare_sub.current_request_start
        assert original_request_start is not None

        # Generate another AGENT_CONFIG while first one is still active
        # libdatadog v26 will return None since already collecting,
        # and subscriber will ignore it
        self.generate_agent_config()

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
        send_request = setup_task_request(flare, *FLARE_REQUEST_DATA)
        flare.send(send_request)
    finally:
        config._trace_writer_native = original_trace_writer_native
        if flare is not None:
            flare.revert_configs()
            flare.clean_up_files()
