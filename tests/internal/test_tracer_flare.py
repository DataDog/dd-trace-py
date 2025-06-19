import logging
from logging import Logger
import multiprocessing
import os
import pathlib
import shutil
from typing import Optional
from unittest import mock

from pyfakefs.fake_filesystem_unittest import TestCase

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
MOCK_FLARE_SEND_REQUEST = FlareSendRequest(case_id="1111111", hostname="myhostname", email="user.name@datadoghq.com")


class MockResponse:
    def __init__(self, status=200, reason="OK", content="Success"):
        self.status = status
        self.reason = reason
        self._content = content.encode('utf-8')

    def read(self):
        return self._content

    def decode(self, encoding='utf-8'):
        return self._content.decode(encoding)


class MockHTTPConnection:
    def __init__(self, *args, **kwargs):
        self.response = MockResponse(200, b"OK")
        self.request_called = False
        self.request_method = None
        self.request_url = None
        self.request_body = None

    @classmethod
    def with_base_path(cls, hostname, port, base_path=None, timeout=None):
        return cls()

    def request(self, method, url, body=None, headers=None, **kwargs):
        self.request_called = True
        self.request_method = method
        self.request_url = url
        self.request_body = body

    def getresponse(self):
        return self.response

    def close(self):
        pass


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

        # Mock the HTTP connection
        self.http_patcher = mock.patch('ddtrace.internal.http.HTTPConnection', MockHTTPConnection)
        self.mock_http = self.http_patcher.start()
        mock_conn = mock.Mock()
        mock_conn_with_base_path = mock.Mock()

        # Create a response object with actual string values
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.reason = "OK"
        mock_bytes = mock.Mock()
        mock_bytes.decode.return_value = "Success"
        mock_response.read.return_value = mock_bytes

        # Set up the response chain
        mock_conn.getresponse.return_value = mock_response
        mock_conn_with_base_path.getresponse.return_value = mock_response
        mock_conn.with_base_path.return_value = mock_conn_with_base_path
        self.mock_http.return_value = mock_conn

    def tearDown(self):
        try:
            self.shared_dir.cleanup()
        except Exception:
            # This will always fail because our Flare logic cleans up the entire directory
            # We're explicitly calling this in tearDown so that we can remove
            # the error log clutter for python < 3.10
            pass
        self.confirm_cleanup()
        self.http_patcher.stop()

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
        assert self._get_handler() is None, "File handler was not removed"


class TracerFlareMultiprocessTests(TestCase):
    def setUp(self):
        self.setUpPyfakefs()
        self.shared_dir = self.fs.create_dir("tracer_flare_test")
        self.errors = multiprocessing.Queue()

        # Mock the HTTP connection
        self.http_patcher = mock.patch('ddtrace.internal.http.HTTPConnection', MockHTTPConnection)
        self.mock_http = self.http_patcher.start()
        mock_conn = mock.Mock()
        mock_conn_with_base_path = mock.Mock()

        # Create a response object with actual string values
        mock_response = mock.Mock()
        mock_response.status = 200
        mock_response.reason = "OK"
        mock_bytes = mock.Mock()
        mock_bytes.decode.return_value = "Success"
        mock_response.read.return_value = mock_bytes

        # Set up the response chain
        mock_conn.getresponse.return_value = mock_response
        mock_conn_with_base_path.getresponse.return_value = mock_response
        mock_conn.with_base_path.return_value = mock_conn_with_base_path
        self.mock_http.return_value = mock_conn

    def tearDown(self):
        self.http_patcher.stop()
        # Clean up the shared directory in the parent process
        if os.path.exists(self.shared_dir.name):
            self.fs.remove_object(self.shared_dir.name)

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

        def handle_agent_config(flare: Flare, error_queue):
            try:
                flare.prepare("DEBUG")
                # Assert that the process wrote its files successfully
                pid = os.getpid()
                expected_files = {
                    f"tracer_python_{pid}.log",
                    f"tracer_config_{pid}.json"
                }
                actual_files = set(os.listdir(self.shared_dir.name))
                if not expected_files.issubset(actual_files):
                    error_queue.put(Exception(f"Files were not generated for pid {pid}. Expected {expected_files}, found {actual_files}"))
            except Exception as e:
                error_queue.put(e)

        def handle_agent_task(flare: Flare, error_queue):
            try:
                flare.send(MOCK_FLARE_SEND_REQUEST)
            except Exception as e:
                error_queue.put(e)

        # Create and run config processes
        config_processes = []
        for i in range(num_processes):
            flare = flares[i]
            p = multiprocessing.Process(target=handle_agent_config, args=(flare, self.errors))
            config_processes.append(p)
            p.start()

        # Wait for all config processes to finish
        for p in config_processes:
            p.join()

        # Check for any errors from config phase
        errors = []
        try:
            while True:
                try:
                    errors.append(self.errors.get_nowait())
                except multiprocessing.queues.Empty:
                    break
        except (EOFError, OSError):
            # Handle case where queue is closed/corrupted
            pass

        if errors:
            raise Exception(f"Errors during config phase: {errors}")

        # Create and run send processes
        send_processes = []
        for i in range(num_processes):
            flare = flares[i]
            p = multiprocessing.Process(target=handle_agent_task, args=(flare, self.errors))
            send_processes.append(p)
            p.start()

        # Wait for all send processes to finish
        for p in send_processes:
            p.join()

        # Check for any errors from send phase
        errors = []
        try:
            while True:
                try:
                    errors.append(self.errors.get_nowait())
                except multiprocessing.queues.Empty:
                    break
        except (EOFError, OSError):
            # Handle case where queue is closed/corrupted
            pass

        if errors:
            raise Exception(f"Errors during send phase: {errors}")

        # Clean up the directory in the parent process
        if os.path.exists(self.shared_dir.name):
            self.fs.remove_object(self.shared_dir.name)

        # Final verification that directory was cleaned up
        assert not os.path.exists(self.shared_dir.name), "Directory was not cleaned up after all processes finished"

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

        def do_tracer_flare(log_level: str, send_request: FlareSendRequest, flare: Flare, error_queue):
            try:
                flare.prepare(log_level)
                if log_level is not None:
                    # For the successful process, verify its files exist
                    pid = os.getpid()
                    expected_files = {
                        f"tracer_python_{pid}.log",
                        f"tracer_config_{pid}.json"
                    }
                    actual_files = set(os.listdir(self.shared_dir.name))
                    if not expected_files.issubset(actual_files):
                        error_queue.put(Exception(f"Files were not generated for pid {pid}. Expected {expected_files}, found {actual_files}"))
                flare.send(send_request)
            except TypeError as e:
                # Expected error for the failing process (invalid log level)
                if log_level is None:
                    return
                error_queue.put(e)
            except Exception as e:
                error_queue.put(e)

        # Create successful process
        p = multiprocessing.Process(target=do_tracer_flare, args=("DEBUG", MOCK_FLARE_SEND_REQUEST, flares[0], self.errors))
        processes.append(p)
        p.start()

        # Create failing process
        p = multiprocessing.Process(target=do_tracer_flare, args=(None, MOCK_FLARE_SEND_REQUEST, flares[1], self.errors))
        processes.append(p)
        p.start()

        # Wait for all processes to finish
        for p in processes:
            p.join()

        # Check for unexpected errors
        errors = []
        try:
            while True:
                try:
                    errors.append(self.errors.get_nowait())
                except multiprocessing.queues.Empty:
                    break
        except (EOFError, OSError):
            # Handle case where queue is closed/corrupted
            pass

        # We expect no errors since the TypeError from the failing process is expected
        assert len(errors) == 0, f"Unexpected errors occurred: {errors}"

        # Clean up the directory in the parent process
        if os.path.exists(self.shared_dir.name):
            self.fs.remove_object(self.shared_dir.name)

        # Verify directory was cleaned up
        assert not os.path.exists(self.shared_dir.name), "Directory was not cleaned up after all processes finished"


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
