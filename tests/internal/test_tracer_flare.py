import logging
from logging import Logger
import multiprocessing
import os
import pathlib
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


DEBUG_LEVEL_INT = logging.DEBUG
TRACE_AGENT_URL = "http://localhost:9126"
MOCK_FLARE_SEND_REQUEST = FlareSendRequest(case_id="1111111", hostname="myhostname", email="user.name@datadoghq.com")


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

    def tearDown(self):
        try:
            self.shared_dir.cleanup()
        except Exception:
            # This will always fail because our Flare logic cleans up the entire directory
            # We're explicitly calling this in tearDown so that we can remove
            # the error log clutter for python < 3.10
            pass
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
        Validte that even if the tracer flare fails for one process, we should
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
            mock_pubsub_conn.return_value = {
                "metadata": [{"product_name": "AGENT_CONFIG"}],
                "config": self.agent_config,
            }
            self.tracer_flare_sub._get_data_from_connector_and_exec()

    def generate_agent_task(self):
        with mock.patch("tests.internal.test_tracer_flare.MockPubSubConnector.read") as mock_pubsub_conn:
            mock_pubsub_conn.return_value = {
                "metadata": [{"product_name": "AGENT_TASK"}],
                "config": self.agent_task,
            }
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
