import logging
from logging import Logger
import multiprocessing
import os
import unittest
from unittest import mock

from ddtrace.internal.flare import TRACER_FLARE_DIRECTORY
from ddtrace.internal.flare import TRACER_FLARE_FILE_HANDLER_NAME
from ddtrace.internal.flare import Flare
from ddtrace.internal.logger import _get_handler
from ddtrace.internal.logger import get_logger


DEBUG_LEVEL_INT = logging.DEBUG


class TracerFlareTests(unittest.TestCase):
    mock_agent_config = [{"name": "flare-log-level", "config": {"log_level": "DEBUG"}}]
    mock_agent_task = [
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
    response = mock.MagicMock()
    response.status = 200
    flare = Flare()

    def setUp(self):
        self.pid = os.getpid()
        self.flare_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_python_{self.pid}.log"
        self.config_file_path = f"{TRACER_FLARE_DIRECTORY}/tracer_config_{self.pid}.json"

    def tearDown(self):
        self.confirm_cleanup()

    def test_single_process_success(self):
        """
        Validate that the baseline tracer flare works for a single process
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
        ddlogger = get_logger("ddtrace")

        self.flare.prepare(self.mock_agent_config)

        file_handler = _get_handler(ddlogger, TRACER_FLARE_FILE_HANDLER_NAME)
        valid_logger_level = self.flare._get_valid_logger_level(DEBUG_LEVEL_INT)
        assert file_handler is not None
        assert file_handler.level == DEBUG_LEVEL_INT
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert os.path.exists(self.config_file_path)

        # Sends request to testagent
        # This just validates the request params
        self.flare.send(self.mock_agent_task)

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
            self.flare.prepare(self.mock_agent_config)

        file_handler = _get_handler(ddlogger, TRACER_FLARE_FILE_HANDLER_NAME)
        assert file_handler is not None
        assert file_handler.level == DEBUG_LEVEL_INT
        assert ddlogger.level == valid_logger_level

        assert os.path.exists(self.flare_file_path)
        assert not os.path.exists(self.config_file_path)

        self.flare.send(self.mock_agent_task)

    def test_multiple_process_success(self):
        """
        Validate that the tracer flare will generate for multiple processes
        """
        processes = []
        num_processes = 3

        def handle_agent_config():
            self.flare.prepare(self.mock_agent_config)

        def handle_agent_task():
            self.flare.send(self.mock_agent_task)

        # Create multiple processes
        for _ in range(num_processes):
            p = multiprocessing.Process(target=handle_agent_config)
            processes.append(p)
            p.start()
        for p in processes:
            p.join()

        # Assert that each process wrote its file successfully
        # We double the process number because each will generate a log file and a config file
        assert len(processes) * 2 == len(os.listdir(TRACER_FLARE_DIRECTORY))

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

        def do_tracer_flare():
            self.flare._prepare(self.mock_agent_config)
            # Assert that only one process wrote its file successfully
            # We check for 2 files because it will generate a log file and a config file
            assert 2 == len(os.listdir(TRACER_FLARE_DIRECTORY))
            self.flare.send(self.mock_agent_task)

        # Create successful process
        p = multiprocessing.Process(target=do_tracer_flare, args=(self.mock_agent_config, self.mock_agent_task))
        processes.append(p)
        p.start()
        # Create failing process
        p = multiprocessing.Process(target=do_tracer_flare, args=(None, self.mock_agent_task))
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
        self.flare.prepare(self.mock_agent_config)

        app_log_line = "this is an app log"
        app_logger.debug(app_log_line)

        assert os.path.exists(self.flare_file_path)

        with open(self.flare_file_path, "r") as file:
            for line in file:
                assert app_log_line not in line, f"File {self.flare_file_path} contains excluded line: {app_log_line}"

        self.flare.clean_up_files()
        self.flare.revert_configs()

    def confirm_cleanup(self):
        ddlogger = get_logger("ddtrace")
        assert not os.path.exists(TRACER_FLARE_DIRECTORY), f"The directory {TRACER_FLARE_DIRECTORY} still exists"
        assert _get_handler(ddlogger, TRACER_FLARE_FILE_HANDLER_NAME) is None
