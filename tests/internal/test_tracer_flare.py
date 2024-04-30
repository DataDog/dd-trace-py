import logging
from logging import Logger
import multiprocessing
import os
import pathlib
from typing import Optional
import unittest
from unittest import mock
import uuid

from ddtrace.internal.flare import TRACER_FLARE_DIRECTORY
from ddtrace.internal.flare import TRACER_FLARE_FILE_HANDLER_NAME
from ddtrace.internal.flare import Flare
from ddtrace.internal.flare import FlareSendRequest
from ddtrace.internal.logger import get_logger


DEBUG_LEVEL_INT = logging.DEBUG


class TracerFlareTests(unittest.TestCase):
    mock_flare_send_request = FlareSendRequest(
        case_id="1111111", hostname="myhostname", email="user.name@datadoghq.com"
    )

    def setUp(self):
        self.flare_uuid = uuid.uuid4()
        self.flare_dir = f"{TRACER_FLARE_DIRECTORY}-{self.flare_uuid}"
        self.flare = Flare(flare_dir=pathlib.Path(self.flare_dir))
        self.pid = os.getpid()
        self.flare_file_path = f"{self.flare_dir}/tracer_python_{self.pid}.log"
        self.config_file_path = f"{self.flare_dir}/tracer_config_{self.pid}.json"

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

        file_handler = self._get_handler()
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)
        assert file_handler is not None, "File handler did not get added to the ddtrace logger"
        assert file_handler.level == DEBUG_LEVEL_INT, "File handler does not have the correct log level"
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert os.path.exists(self.config_file_path)

        # Sends request to testagent
        # This just validates the request params
        self.flare.send(self.mock_flare_send_request)

    def test_single_process_partial_failure(self):
        """
        Validate that even if one of the files fails to be generated,
        we still attempt to send the flare with partial info (ensure best effort)
        """
        ddlogger = get_logger("ddtrace")
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)

        # Mock the partial failure
        with mock.patch("json.dump") as mock_json:
            mock_json.side_effect = Exception("file issue happened")
            self.flare.prepare("DEBUG")

        file_handler = self._get_handler()
        assert file_handler is not None
        assert file_handler.level == DEBUG_LEVEL_INT
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert not os.path.exists(self.config_file_path)

        self.flare.send(self.mock_flare_send_request)

    def test_multiple_process_success(self):
        """
        Validate that the tracer flare will generate for multiple processes
        """
        processes = []
        num_processes = 3

        def handle_agent_config():
            self.flare.prepare("DEBUG")

        def handle_agent_task():
            self.flare.send(self.mock_flare_send_request)

        # Create multiple processes
        for _ in range(num_processes):
            p = multiprocessing.Process(target=handle_agent_config)
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        # Assert that each process wrote its file successfully
        # We double the process number because each will generate a log file and a config file
        assert len(processes) * 2 == len(os.listdir(self.flare_dir))

        for _ in range(num_processes):
            p = multiprocessing.Process(target=handle_agent_task)
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

    def test_multiple_process_partial_failure(self):
        """
        Validte that even if the tracer flare fails for one process, we should
        still continue the work for the other processes (ensure best effort)
        """
        processes = []

        def do_tracer_flare(prep_request, send_request):
            self.flare.prepare(prep_request)
            # Assert that only one process wrote its file successfully
            # We check for 2 files because it will generate a log file and a config file
            assert 2 == len(os.listdir(self.flare_dir))
            self.flare.send(send_request)

        # Create successful process
        p = multiprocessing.Process(target=do_tracer_flare, args=("DEBUG", self.mock_flare_send_request))
        processes.append(p)
        p.start()
        # Create failing process
        p = multiprocessing.Process(target=do_tracer_flare, args=(None, self.mock_flare_send_request))
        processes.append(p)
        p.start()
        for p in processes:
            p.join()

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
