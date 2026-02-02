import dataclasses
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import pathlib
import shutil
import time
from typing import Optional
import zipfile

from ddtrace import config
from ddtrace._logger import _add_file_handler
from ddtrace._logger import _configure_ddtrace_native_logger
from ddtrace.internal.flare.json_formatter import StructuredJSONFormatter
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import logger as native_logger
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.native._native import register_tracer_flare as native_flare  # type: ignore

TRACER_FLARE_DIRECTORY = "tracer_flare"
TRACER_FLARE_ZIP = pathlib.Path("tracer_flare.zip")
TRACER_FLARE_ENDPOINT = "/tracer_flare/v1"
TRACER_FLARE_FILE_HANDLER_NAME = "tracer_flare_file_handler"
TRACER_FLARE_LOCK = pathlib.Path("tracer_flare.lock")
DEFAULT_TIMEOUT_SECONDS = 5

log = get_logger(__name__)

class TracerFlareSendError(Exception):
    pass


class Flare:
    def __init__(
        self,
        trace_agent_url: str,
        ddconfig: dict,
        api_key: Optional[str] = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SECONDS,
        flare_dir: str = TRACER_FLARE_DIRECTORY,
    ):
        self.original_log_level: int = logging.NOTSET
        self.timeout: int = timeout_sec
        self.flare_dir: pathlib.Path = pathlib.Path(flare_dir)
        self.file_handler: Optional[RotatingFileHandler] = None
        self.url: str = trace_agent_url
        self._api_key: Optional[str] = api_key
        self.ddconfig = ddconfig

        self._create_native_manager()

    def _create_native_manager(self):
        """Create or recreate the native manager to ensure clean state."""
        self._native_manager = native_flare.TracerFlareManager(agent_url=self.url, language="python")

    def prepare(self, log_level: str) -> bool:
        """
        Update configurations to start sending tracer logs to a file
        to be sent in a flare later.
        """
        try:
            self.flare_dir.mkdir(exist_ok=True)
        except Exception as e:
            log.error("Flare prepare: failed to create %s directory: %s", self.flare_dir, e)
            return False

        if not isinstance(log_level, str):
            log.error("Flare prepare: Invalid log level provided: %s (must be a string)", log_level)
            return False

        flare_log_level_int = getattr(logging, log_level.upper(), None)
        if flare_log_level_int is None or not isinstance(flare_log_level_int, int):
            log.error("Flare prepare: Invalid log level provided: %s", log_level)
            return False

        # Setup logging and create config file
        self._setup_flare_logging(flare_log_level_int)
        return True

    def send(self, return_action: native_flare.ReturnAction):
        """
        Revert tracer flare configurations back to original state
        before sending the flare.
        """
        # Always revert configs and cleanup, even for invalid requests
        try:
            if return_action.is_send() is False:
                return
            self.revert_configs()

            # Ensure the flare directory exists (it might have been deleted by clean_up_files)
            self.flare_dir.mkdir(exist_ok=True)

            self._send_flare_request(return_action)
        finally:
            self.clean_up_files()
            # Recreate the native manager to reset its state for the next flare
            self._create_native_manager()

    def revert_configs(self):
        ddlogger = get_logger("ddtrace")
        if self.file_handler:
            ddlogger.removeHandler(self.file_handler)
            log.debug("ddtrace logs will not be routed to the %s file handler anymore", TRACER_FLARE_FILE_HANDLER_NAME)
        else:
            log.debug("Could not find %s to remove", TRACER_FLARE_FILE_HANDLER_NAME)
        ddlogger.setLevel(self.original_log_level)

        # Restore native logger configuration from env vars
        if config._trace_writer_native:
            try:
                native_logger.disable("file")
            except ValueError:
                log.debug("Native file logger is not enabled")
            _configure_ddtrace_native_logger()

    def _setup_flare_logging(self, flare_log_level_int: int) -> int:
        """
        Setup flare logging configuration.
        Returns the process ID.
        """
        ddlogger = get_logger("ddtrace")
        pid = os.getpid()
        flare_file_path = self.flare_dir / f"tracer_python_{pid}.log"
        self.original_log_level = ddlogger.level

        # Set the logger level to the more verbose between original and flare
        # We do this valid_original_level check because if the log level is NOTSET, the value is 0
        # which is the minimum value. In this case, we just want to use the flare level, but still
        # retain the original state as NOTSET/0
        valid_original_level = (
            logging.CRITICAL if self.original_log_level == logging.NOTSET else self.original_log_level
        )
        logger_level = min(valid_original_level, flare_log_level_int)
        ddlogger.setLevel(logger_level)

        # Use structured JSON formatter for flare logs
        json_formatter = StructuredJSONFormatter()
        self.file_handler = _add_file_handler(
            ddlogger,
            flare_file_path.__str__(),
            flare_log_level_int,
            TRACER_FLARE_FILE_HANDLER_NAME,
            formatter=json_formatter,
        )

        if config._trace_writer_native:
            native_flare_path = self.flare_dir / f"tracer_native_{pid}.log"
            native_logger.configure(output="file", path=str(native_flare_path))
            native_logger.set_log_level(logging.getLevelName(flare_log_level_int))

        return pid

    def _get_valid_logger_level(self, flare_log_level: int) -> int:
        valid_original_level = 100 if self.original_log_level == 0 else self.original_log_level
        return min(valid_original_level, flare_log_level)

    def _send_flare_request(self, return_action: native_flare.ReturnAction):
        """
        Send the flare request to the agent.
        """
        # We only want the flare to be sent once, even if there are
        # multiple tracer instances
        lock_path = self.flare_dir / TRACER_FLARE_LOCK
        if not os.path.exists(lock_path):
            open(lock_path, "w").close()

            # Use native implementation
            log.debug("Sending tracer flare using native implementation")

            # Use native zip_and_send
            self._native_manager.zip_and_send(str(self.flare_dir.absolute()), return_action)  # type: ignore
            log.info("Successfully sent the flare to Zendesk ticket %s", return_action.case_id)


    def clean_up_files(self):
        """Clean up the flare directory using Python's shutil."""
        try:
            shutil.rmtree(self.flare_dir)
        except Exception as e:
            log.warning("Failed to clean up tracer flare files: %s", e)
